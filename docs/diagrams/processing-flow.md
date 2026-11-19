# Fluxo de processamento e confirmação

```mermaid
flowchart TD
    A["Frame recebido com sequência e timestamp"] --> B{"Novo e recente?"}
    B -->|Não| C["Descartar para análise temporal"]
    B -->|Sim| D["Selecionar canais menos recentemente analisados"]
    D --> E["Preparar lote de até quatro imagens"]
    E --> F["Pose YOLO e verificação de pessoa"]
    F --> G["Restaurar coordenadas e associar pessoas"]
    G --> H["Aplicar zonas, perspectiva e características temporais"]
    H --> I{"Estado alert_pending?"}
    I -->|Não| J["Suspeita em memória; sem MP4 confirmado"]
    I -->|Sim| K["Marcar segmento confirmado"]
    K --> L["Concluir após recuperação, limite ou encerramento"]
    L --> M["Persistir MP4 e aplicar retenção de não protegidos"]
    I -->|Sim| N{"Canal e notificações elegíveis?"}
    N -->|Sim| O["Registrar alerta, imagem e fila Telegram"]
    M -->|"Se houver alerta elegível"| O
    O --> P["Converter cópia H.264 e atualizar mensagem"]
    P --> Q["Recebimento e manutenção das mensagens conhecidas"]
```

Captura, análise, desenho e gravação têm cadências diferentes. A confirmação não espera o upload e o recebimento não valida o bem-estar da pessoa. Perda de evidência reinicia o tempo no chão; a caixa suavizada da apresentação não entra nesse cálculo.
