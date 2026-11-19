const systemState = document.querySelector("#system-state");
const onlineCount = document.querySelector("#online-count");
const peopleCount = document.querySelector("#people-count");
const aiLatency = document.querySelector("#ai-latency");
const clock = document.querySelector("#clock");
const mosaicImage = document.querySelector("#mosaic-image");
const mosaicHotspots = document.querySelector("#mosaic-hotspots");
const mosaicLoading = document.querySelector("#mosaic-loading");
const viewer = document.querySelector("#viewer");
const viewerImage = document.querySelector("#viewer-image");
const viewerTitle = document.querySelector("#viewer-title");
const viewerChannel = document.querySelector("#viewer-channel");
const viewerPosition = document.querySelector("#viewer-position");
const ptzPanel = document.querySelector("#ptz-panel");
const fallPanel = document.querySelector("#fall-panel");
const fallState = document.querySelector("#fall-state");
const fallChannel = document.querySelector("#fall-channel");
const fallScore = document.querySelector("#fall-score");
const fallAngle = document.querySelector("#fall-angle");
const fallSpeed = document.querySelector("#fall-speed");

let cameras = [];
let ai = { ready: false, warmingUp: true, totalPeople: 0 };
let selectedIndex = 0;
let ptzQueue = Promise.resolve();
let movingPtzButton = null;

function updateClock() {
  clock.textContent = new Intl.DateTimeFormat("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function startMosaic() {
  if (document.hidden || viewer.open) return;
  mosaicLoading.hidden = false;
  mosaicImage.src = `/api/mosaic.mjpg?t=${Date.now()}`;
  setTimeout(() => { mosaicLoading.hidden = true; }, 900);
}

function stopMosaic() {
  mosaicImage.removeAttribute("src");
}

function startViewerStream() {
  if (!viewer.open || !cameras.length) return;
  const camera = cameras[selectedIndex];
  viewerImage.src = `/api/cameras/${camera.id}/stream.mjpg?t=${Date.now()}`;
}

mosaicImage.addEventListener("load", () => { mosaicLoading.hidden = true; });

function cameraLabel(camera) {
  const personText = camera.personCount === 1 ? "1 pessoa" : `${camera.personCount || 0} pessoas`;
  const fallText = camera.fall?.enabled ? `, queda: ${fallStateLabel(camera.fall.state)}` : "";
  return `Canal ${camera.id}, ${camera.name}, ${camera.online ? "online" : "offline"}, ${personText}${fallText}`;
}

function fallStateLabel(state) {
  return {
    normal: "normal",
    possible_fall: "possível queda",
    down: "pessoa no chão",
    alert_pending: "alerta pendente",
  }[state] || "calibrando";
}

function fallStateDisplay(state) {
  return {
    normal: "NORMAL",
    possible_fall: "POSSÍVEL QUEDA",
    down: "NO CHÃO",
    alert_pending: "ALERTA PENDENTE",
  }[state] || "CALIBRANDO";
}

function renderHotspots() {
  mosaicHotspots.replaceChildren(
    ...cameras.map((camera) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "mosaic-hotspot";
      button.dataset.cameraId = camera.id;
      button.setAttribute("aria-label", cameraLabel(camera));
      button.title = `Ampliar ${camera.name}`;
      const text = document.createElement("span");
      text.className = "sr-only";
      text.textContent = cameraLabel(camera);
      button.append(text);
      button.addEventListener("click", () => openViewer(camera.id));
      return button;
    }),
  );
}

function updateHotspots() {
  cameras.forEach((camera) => {
    const button = mosaicHotspots.querySelector(`[data-camera-id="${camera.id}"]`);
    if (!button) return;
    button.classList.toggle("has-person", camera.personCount > 0);
    button.classList.toggle("offline", !camera.online);
    button.classList.toggle("fall-possible", camera.fall?.state === "possible_fall");
    button.classList.toggle("fall-down", camera.fall?.state === "down");
    button.classList.toggle("fall-alert", camera.fall?.state === "alert_pending");
    button.setAttribute("aria-label", cameraLabel(camera));
    button.querySelector(".sr-only").textContent = cameraLabel(camera);
  });
}

function updateSummary() {
  const online = cameras.filter((camera) => camera.online).length;
  const totalPeople = cameras.reduce((total, camera) => total + (camera.personCount || 0), 0);
  onlineCount.textContent = `${online}/${cameras.length || 9}`;
  peopleCount.textContent = totalPeople;
  aiLatency.textContent = ai.ready ? Math.round(ai.inferenceMs || 0) : "…";
  document.body.classList.toggle("occupied", totalPeople > 0);

  if (ai.error) {
    systemState.textContent = "IA indisponível";
  } else if (!ai.ready) {
    systemState.textContent = "Aquecendo YOLO na GPU";
  } else if (online !== cameras.length) {
    systemState.textContent = `${online} de ${cameras.length} câmeras online`;
  } else if (totalPeople > 0) {
    systemState.textContent = totalPeople === 1 ? "1 pessoa detectada" : `${totalPeople} pessoas detectadas`;
  } else {
    systemState.textContent = "IA ativa · ambiente sem pessoas";
  }
}

async function loadStatus(initial = false) {
  try {
    const response = await fetch("/api/cameras", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    cameras = payload.cameras;
    ai = payload.ai;
    if (initial || mosaicHotspots.children.length !== cameras.length) renderHotspots();
    updateHotspots();
    updateSummary();
    if (viewer.open) showSelectedCamera(false);
  } catch (error) {
    systemState.textContent = "Painel sem conexão";
    console.error("Falha ao atualizar o estado", error);
  }
}

function showSelectedCamera(refreshFrame = true) {
  const camera = cameras[selectedIndex];
  if (!camera) return;
  viewerTitle.textContent = camera.name;
  const people = camera.personCount || 0;
  const presence = people ? ` · ${people} ${people === 1 ? "PESSOA" : "PESSOAS"}` : "";
  const fallStatus = camera.fall?.enabled
    ? ` · QUEDA ${fallStateDisplay(camera.fall.state)}`
    : "";
  viewerChannel.textContent = `CANAL ${String(camera.id).padStart(2, "0")}${presence}${fallStatus}`;
  viewerPosition.textContent = `${selectedIndex + 1} / ${cameras.length}`;
  viewerImage.alt = `Visualização ampliada do canal ${camera.id}: ${camera.name}`;
  ptzPanel.toggleAttribute("hidden", !camera.ptz);
  updateFallPanel(camera);
  if (refreshFrame) startViewerStream();
}

function updateFallPanel(camera) {
  const fall = camera.fall;
  fallPanel.toggleAttribute("hidden", !fall?.enabled);
  if (!fall?.enabled) return;

  fallChannel.textContent = `CALIBRAÇÃO DE QUEDA · CH ${String(camera.id).padStart(2, "0")}`;
  const features = fall.features || {};
  const score = Math.max(0, Math.min(1, Number(features.score) || 0));
  fallPanel.dataset.state = fall.state || "normal";
  fallState.textContent = fallStateDisplay(fall.state);
  fallScore.textContent = `${Math.round(score * 100)}%`;
  fallAngle.textContent = Number.isFinite(Number(features.torso_angle))
    ? `${Math.round(Number(features.torso_angle))}°`
    : "—";
  fallSpeed.textContent = Number.isFinite(Number(features.center_speed))
    ? Number(features.center_speed).toFixed(2)
    : "—";
}

function openViewer(channelId) {
  selectedIndex = cameras.findIndex((camera) => camera.id === channelId);
  if (selectedIndex < 0) return;
  stopMosaic();
  viewer.showModal();
  showSelectedCamera();
}

function sendPtz(action) {
  const camera = cameras[selectedIndex];
  if (!camera?.ptz) return Promise.resolve();
  ptzQueue = ptzQueue
    .then(async () => {
      const response = await fetch(`/api/cameras/${camera.id}/ptz`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (!response.ok) throw new Error(`PTZ HTTP ${response.status}`);
    })
    .catch((error) => console.error("Falha no controle PTZ", error));
  return ptzQueue;
}

function stopPtz() {
  if (!movingPtzButton) return;
  movingPtzButton.classList.remove("active");
  movingPtzButton = null;
  sendPtz("stop");
}

function closeViewer() {
  stopPtz();
  viewerImage.removeAttribute("src");
  viewer.close();
  startMosaic();
}

document.querySelector("#viewer-close").addEventListener("click", closeViewer);
document.querySelector("#viewer-prev").addEventListener("click", () => {
  stopPtz();
  selectedIndex = (selectedIndex - 1 + cameras.length) % cameras.length;
  showSelectedCamera();
});
document.querySelector("#viewer-next").addEventListener("click", () => {
  stopPtz();
  selectedIndex = (selectedIndex + 1) % cameras.length;
  showSelectedCamera();
});
viewer.addEventListener("click", (event) => {
  if (event.target === viewer) closeViewer();
});
viewer.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeViewer();
});
ptzPanel.querySelectorAll("[data-ptz]").forEach((button) => {
  const action = button.dataset.ptz;
  if (action === "home") {
    button.addEventListener("click", () => sendPtz("home"));
    return;
  }
  button.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    stopPtz();
    movingPtzButton = button;
    button.classList.add("active");
    button.setPointerCapture(event.pointerId);
    sendPtz(action);
  });
  button.addEventListener("pointerup", stopPtz);
  button.addEventListener("pointercancel", stopPtz);
  button.addEventListener("lostpointercapture", stopPtz);
  button.addEventListener("contextmenu", (event) => event.preventDefault());
});
document.addEventListener("keydown", (event) => {
  if (!viewer.open) return;
  if (event.key === "ArrowLeft") document.querySelector("#viewer-prev").click();
  if (event.key === "ArrowRight") document.querySelector("#viewer-next").click();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopMosaic();
    viewerImage.removeAttribute("src");
    return;
  }
  if (viewer.open) startViewerStream();
  else startMosaic();
});

updateClock();
setInterval(updateClock, 1000);
loadStatus(true).then(startMosaic);
setInterval(() => loadStatus(false), 750);
