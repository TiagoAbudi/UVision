from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.request import (
    HTTPDigestAuthHandler, HTTPPasswordMgrWithDefaultRealm, build_opener,
    parse_http_list, parse_keqv_list,
)

LOGGER = logging.getLogger(__name__)
HEALTH_LABELS = {
    "connecting": "Conectando", "online": "Online", "stalled": "Sem quadros recentes",
    "network_error": "Equipamento sem resposta", "auth_error": "Credenciais rejeitadas",
    "stream_error": "Stream indisponível", "reconnecting": "Reconectando", "stopped": "Encerrado",
}


def probe_rtsp(url: str, timeout: float = 2.0) -> str:
    """Check reachability and authentication without logging a credential-bearing URL."""
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 554
    if not host:
        return "network_error"
    uri = urlunsplit((parts.scheme, f"{host}:{port}", parts.path, parts.query, ""))

    def request(authorization: str = "") -> tuple[int, dict[str, str]]:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            headers = f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n"
            if authorization:
                headers += f"Authorization: {authorization}\r\n"
            connection.sendall((headers + "\r\n").encode())
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 16384:
                block = connection.recv(4096)
                if not block:
                    break
                data += block
        lines = data.decode("latin-1").split("\r\n")
        code = int(lines[0].split()[1])
        return code, {key.lower(): value.strip() for key, separator, value in
                      (line.partition(":") for line in lines[1:]) if separator}

    try:
        code, headers = request()
        if code == 403:
            return "auth_error"
        if code != 401:
            return "stream_error"
        challenge = headers.get("www-authenticate", "")
        user, password = unquote(parts.username or ""), unquote(parts.password or "")
        if challenge.lower().startswith("basic "):
            authorization = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        elif challenge.lower().startswith("digest "):
            values = parse_keqv_list(parse_http_list(challenge[7:]))
            algorithm = values.get("algorithm", "MD5").upper()
            if algorithm not in {"MD5", "SHA-256"}:
                return "stream_error"
            digest = lambda value: hashlib.new(algorithm.replace("-", "").lower(), value.encode()).hexdigest()
            nonce, realm = values["nonce"], values.get("realm", "")
            ha1, ha2 = digest(f"{user}:{realm}:{password}"), digest(f"DESCRIBE:{uri}")
            qops = {value.strip() for value in values.get("qop", "").split(",")}
            cnonce = secrets.token_hex(8)
            if values.get("qop") and "auth" not in qops:
                return "stream_error"
            response = digest(f"{ha1}:{nonce}:00000001:{cnonce}:auth:{ha2}") if "auth" in qops else digest(f"{ha1}:{nonce}:{ha2}")
            authorization = f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{response}", algorithm={algorithm}'
            if "auth" in qops:
                authorization += f', qop=auth, nc=00000001, cnonce="{cnonce}"'
            if "opaque" in values:
                authorization += f', opaque="{values["opaque"]}"'
        else:
            return "stream_error"
        code, _ = request(authorization)
        return "auth_error" if code in {401, 403} else "stream_error"
    except OSError:
        return "network_error"
    except (ValueError, KeyError, IndexError):
        return "stream_error"


def discover_onvif(reference_host: str, timeout: float = 3.0) -> list[str]:
    network = ipaddress.ip_network(reference_host + "/24", strict=False)
    payload = f'''<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" xmlns:n="http://www.onvif.org/ver10/network/wsdl"><e:Header><w:MessageID>uuid:{uuid.uuid4()}</w:MessageID><w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To><w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header><e:Body><d:Probe><d:Types>n:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>'''.encode()
    found: set[str] = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect((reference_host, 3702))
        local = route.getsockname()[0]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as connection:
        connection.bind((local, 0))
        connection.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
        connection.settimeout(0.5)
        connection.sendto(payload, ("239.255.255.250", 3702))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, (host, _) = connection.recvfrom(65535)
                if ipaddress.ip_address(host) in network and b"ProbeMatch" in data:
                    ET.fromstring(data)
                    found.add(host)
            except socket.timeout:
                continue
            except (ValueError, ET.ParseError):
                continue
    return sorted(found)


def dvr_identity(host: str, user: str, password: str) -> str | None:
    base = f"http://{host}"
    manager = HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, base, user, password)
    with build_opener(HTTPDigestAuthHandler(manager)).open(
        base + "/cgi-bin/magicBox.cgi?action=getSystemInfo", timeout=3,
    ) as response:
        values = dict(line.partition("=")[::2] for line in response.read(65536).decode("utf-8", "replace").splitlines() if "=" in line)
    serial = values.get("serialNumber", "").strip()
    return hashlib.sha256(serial.encode()).hexdigest() if serial else None


class DVRRecovery:
    """Pair once by identity; recover IP only when that same recorder is found."""

    def __init__(self, settings, feeds: list, state_path: Path) -> None:
        self.settings, self.feeds, self.state_path = settings, feeds, state_path
        self.host = settings.host
        self.identity: str | None = None
        self.message = "Identificando o DVR"
        self._stop = threading.Event()
        self._requested = threading.Event()
        self._thread = threading.Thread(target=self._run, name="dvr-recovery", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._requested.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def request(self) -> None:
        self._requested.set()

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"configuredHost": self.settings.host, "host": self.host, "identity": self.identity})
        if self.state_path.exists() and self.state_path.read_text(encoding="utf-8") == payload:
            return
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self.state_path)

    def _apply(self, host: str) -> None:
        # Only the host changes. Channels, stream options and credentials stay intact.
        for feed in self.feeds[:self.settings.channel_count]:
            parts = urlsplit(feed.url)
            userinfo, separator, _ = parts.netloc.rpartition("@")
            authority = (userinfo + separator if separator else "") + host + f":{parts.port or 554}"
            feed.reconnect(urlunsplit(parts._replace(netloc=authority)))
        self.host = host

    def check_once(self) -> None:
        try:
            current = dvr_identity(self.host, self.settings.user, self.settings.password)
        except OSError:
            current = None
        if current and (self.identity is None or current == self.identity):
            self.identity = current
            self.message = "Identidade do DVR confirmada"
            self._save()
            return
        if not self.identity:
            self.message = "DVR sem resposta ou identificação; endereço ainda não pareado"
            return
        self.message = "Procurando o DVR pareado na rede local"
        try:
            hosts = discover_onvif(self.host)
        except (OSError, ValueError):
            self.message = "Busca de rede indisponível; uma nova tentativa será feita"
            return
        for host in hosts:
            if self._stop.is_set():
                break
            try:
                if dvr_identity(host, self.settings.user, self.settings.password) == self.identity:
                    self._apply(host)
                    self._save()
                    self.message = f"DVR reencontrado em {host}"
                    LOGGER.info("DVR reencontrado por identidade em %s", host)
                    return
            except OSError:
                continue
        self.message = "DVR pareado ainda não localizado; mantendo a configuração"

    def _run(self) -> None:
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if data.get("configuredHost") == self.settings.host:
                    self.identity = data.get("identity")
                    if self.identity and data.get("host") != self.host:
                        ipaddress.IPv4Address(data["host"])
                        self._apply(data["host"])
            while not self._stop.is_set():
                try:
                    self.check_once()
                except OSError:
                    self.message = "Falha ao salvar a identidade; confira o armazenamento local"
                self._requested.wait(30)
                self._requested.clear()
        except (OSError, ValueError, KeyError):
            self.message = "Não foi possível recuperar o DVR; consulte o diagnóstico"
            LOGGER.warning("Recuperação de rede indisponível; verifique conexão e estado local")
