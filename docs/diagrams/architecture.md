# Componentes e fronteiras

Este diagrama detalha a [arquitetura principal](../architecture.md). O servidor HTTP legado não participa desta entrada desktop.

```mermaid
flowchart LR
    subgraph LAN["Rede administrada pelo usuário"]
        CAM["DVR e câmera extra"]
    end
    subgraph PC["Computador Windows"]
        CAP["CameraFeed · RTSP TCP"]
        AI["DetectionWorker / YOLO"]
        MON["Monitores temporais por pessoa"]
        VIS["Tracking visual para caixas"]
        VID["Gravadores por câmera"]
        JOURNAL["EventStore"]
        CMD["PresentationBridge"]
        VIEW["Qt WebEngine / React"]
        DISK[("Dados privados em disco")]
        NOT["NotificationService"]
    end
    CAM --> CAP
    CAP --> AI --> MON
    CAP --> VIS
    AI --> VIS
    CAP --> VID
    MON --> VID
    MON --> NOT
    VID --> JOURNAL
    NOT <--> JOURNAL
    JOURNAL --> DISK
    VID --> DISK
    VIS --> CMD
    MON --> CMD
    JOURNAL --> CMD
    CMD <-->|"QWebChannel"| VIEW
    CMD -->|"Imagens locais uvision"| VIEW
    NOT <-->|"HTTPS / polling"| TG["Telegram"]
```

A rede de câmeras pode usar protocolos sem criptografia de transporte; a execução local não torna essa rede automaticamente segura. O único serviço externo mostrado é o Telegram opcional. Não existe reconhecimento facial ou identidade biométrica neste fluxo.
