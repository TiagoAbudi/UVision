# Configuração

O arquivo público é [`.env.example`](../.env.example). O `.env` real não deve ser versionado. Os valores do exemplo são escolhas iniciais seguras; a coluna “default interno” abaixo descreve o código quando uma variável não existe.

## Localização e precedência

`DVRSettings.load()` e `Settings.load()` leem primeiro `.env` na pasta pai do projeto com `override=False` e depois `.env` dentro do projeto com `override=True`. Assim, variáveis do processo prevalecem sobre o primeiro arquivo, mas o segundo pode substituí-las.

O serviço Telegram lê e salva **o `.env` da pasta pai**. Para evitar configurações divergentes, mantenha um único arquivo nessa pasta, como descrito na [instalação](installation.md). A interface conserva o token existente quando o campo é deixado vazio; nunca devolve o token salvo ao frontend. As credenciais ficam em texto no arquivo local, não em um cofre criptografado.

Os diretórios de trabalho são resolvidos a partir dos fontes instalados em modo editável, não da pasta atual do terminal. Não há variável pública para trocar todos os diretórios de dados de uma vez.

## DVR e câmera adicional

| Variável | Default interno | Uso |
|---|---|---|
| `DVR_HOST` | obrigatório | Host/IP do DVR; sem esquema ou credenciais. |
| `DVR_USER`, `DVR_PASSWORD` | obrigatórios | Credenciais para RTSP, títulos e identificação do DVR. |
| `DVR_CHANNEL_COUNT` | `8` | Canais do DVR. A interface atual é integrada para até nove posições totais. |
| `EXTRA_CAMERA_HOST` | vazio | Habilita uma câmera adicional no canal seguinte ao último do DVR. |
| `EXTRA_CAMERA_NAME` | `Câmera adicional` | Nome apresentado para a câmera extra. |

A câmera adicional usa as mesmas credenciais do DVR e um caminho RTSP específico. Não existe formulário genérico de URL por câmera no desktop atual. Veja [compatibilidade](cameras.md).

## Modelos e desempenho

| Variável | Default interno | Uso |
|---|---|---|
| `YOLO_MODEL` | `yolo26n.pt` | Caminho/nome do modelo principal. Para quedas, use `yolo26s-pose.pt` conforme o exemplo. |
| `YOLO_DEVICE` | `auto` | Seleciona GPU disponível ou CPU; também aceita `cpu` ou índice CUDA, como `0`. |
| `YOLO_IMAGE_SIZE` | `640` | Resolução alvo da inferência; perfil valida múltiplos de 32 entre 320 e 960. |
| `CONFIDENCE_THRESHOLD` | `0.50` | Confiança mínima de detecção; não é probabilidade de queda. |
| `ANIMAL_FILTER_ENABLED` | `true` | Verificação adicional com `yolo26s.pt` junto ao modelo principal. |
| `DVR_DETECTION_INTERVAL` | `0.30` | Intervalo alvo do worker em segundos; não é garantia de FPS por câmera. |

Um perfil salvo em `runtime/performance.json` prevalece na operação sobre os parâmetros iniciais de resolução/intervalo. A página Observabilidade aplica os perfis explicitamente; editar `.env` não substitui um perfil já salvo. Consulte [métricas e perfis](ai-detection.md).

## Quedas e calibração

| Variável | Default interno | Uso |
|---|---|---|
| `FALL_MONITOR_CHANNEL` | `4` | Canal piloto; recebe os defaults de rotação e geometria abaixo. Não é o único canal analisado. |
| `FALL_MONITOR_CHANNELS` | `all` | Todos os disponíveis ou lista sem duplicatas, como `1,4,9`. |
| `FALL_SHADOW_MODE` | `true` | Identifica eventos como testes de calibração. O Telegram só os envia se a opção correspondente for permitida. |
| `FALL_CAMERA_ROTATION` | `0` | Rotação do canal piloto: 0/90/180/270 graus. |
| `FALL_CAMERA_ROTATION_CHXX` | conforme canal | Ajuste individual, por exemplo `CH01`; os demais canais usam 0 quando não há ajuste. |
| `FALL_GROUND_CONFIRM_SECONDS` | `3` | Tempo de evidência contínua no chão; a UI aceita de 1 a 30 segundos. |
| `FALL_GEOMETRY_PROFILE` | `calibration/ch04-geometry.json` | Perfil do canal piloto. **Use vazio em uma instalação nova**, até existir uma calibração própria. |
| `FALL_GEOMETRY_PROFILE_CHXX` | descoberta local | Perfil individual. Vazio explícito desativa; sem variável, busca `calibration/chXX-geometry.json` para os outros canais. |

Zonas salvas em `calibration/chXX-zones.json` têm precedência para orientação e tempo no chão. O perfil geométrico precisa declarar o mesmo canal. Configuração numérica válida não significa enquadramento calibrado ou detecção confiável. [Procedimento completo](calibration.md).

## Gravações

| Variável | Default interno | Efeito |
|---|---|---|
| `FALL_VIDEO_ENABLED` | `true` | Habilita o gravador por canal monitorado. |
| `FALL_VIDEO_FPS` | `8` | Cadência do gravador; limitada internamente a 1–15 FPS. |
| `FALL_VIDEO_PRE_SECONDS` | `8` | Buffer anterior em memória; limitado a 0–30 segundos. |
| `FALL_VIDEO_POST_SECONDS` | `8` | Tempo após retornar ao estado normal; limitado a 1–60 segundos. |
| `FALL_VIDEO_MAX_FILES` | `30` | Quantidade de clipes **não protegidos por câmera**, mínimo 1. |

Não é gravação contínua 24/7. Há limite adicional de aproximadamente 120 segundos de frames por segmento. Protegidos e clipes vinculados a entregas pendentes podem exceder a contagem. [Armazenamento e retenção](recordings.md).

## Telegram

| Variável | Default interno | Efeito |
|---|---|---|
| `TELEGRAM_ENABLED` | `false` | Ativa os envios automáticos. |
| `TELEGRAM_BOT_TOKEN` | vazio | Segredo fornecido pelo BotFather. |
| `TELEGRAM_CHAT_IDS` | vazio | Até oito IDs numéricos, separados por vírgula. |
| `TELEGRAM_CHANNELS` | `4` | Lista de canais de 1 a 9; o exemplo seleciona os nove. |
| `TELEGRAM_ALLOW_CALIBRATION` | `false` | Permite enviar eventos em shadow mode, identificados como TESTE. |
| `TELEGRAM_REMINDER_SECONDS` | `90` | Intervalo: 30–3600 segundos. |
| `TELEGRAM_MAX_REMINDERS` | `3` | Até cinco lembretes; 0 desativa somente os lembretes. |

Selecionar um canal não habilita sua análise, não liga a câmera e não causa envio se não houver confirmação. `FALL_MONITOR_CHANNELS` e `TELEGRAM_CHANNELS` têm funções diferentes. [Configuração do bot](alerts.md).

## Modos legados

`DVR_WEB_HOST=127.0.0.1`, `DVR_WEB_PORT=8765` e `DVR_MOSAIC_FPS=6` pertencem ao painel HTTP/compositor legado. O desktop 3.0 não inicia esse servidor nem usa sua taxa de mosaico.

O modo `python -m camera_ai` é um experimento de câmera única: exige `CAMERA_URL`, `CAMERA_USER`, `CAMERA_PASSWORD`; a URL precisa ser RTSP sem credenciais embutidas. Usa ainda `PRESENCE_CONFIRM_FRAMES=3`, `ABSENCE_CONFIRM_FRAMES=10`, `DETECTION_COOLDOWN=30`, `DISPLAY_ENABLED=true`, `SNAPSHOT_ON_EVENT=false`, `LIGHT_AUTOMATION_ENABLED=false` e `LIGHT_COMMAND_TIMEOUT=15`. A automação de iluminação exige um script Node externo ao repositório da câmera e não é requisito do desktop.

Não use essas variáveis para tentar adicionar streams arbitrários ao mosaico multicâmera.
