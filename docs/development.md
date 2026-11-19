# Desenvolvimento

## Organização dos fontes

```text
camera-ai/
├── src/camera_ai/
│   ├── desktop_dashboard.py     # entrada, logging e instância única
│   ├── hybrid_ui.py             # janela Qt e esquema uvision
│   ├── presentation.py          # QWebChannel, estado, imagens e player
│   ├── presentation_actions.py  # validação e comandos da apresentação
│   ├── web_dashboard.py         # backend compartilhado e entrada HTTP legada
│   ├── camera/                 # RTSP, saúde e recuperação
│   ├── detection/              # YOLO, tracking, zonas, perspectiva e estados
│   ├── recording/              # buffers, MP4 e exportação Telegram
│   ├── notifications.py        # envio e polling Telegram
│   ├── telegram_layout.py      # textos e legendas
│   ├── event_store.py          # histórico/revisões/proteção
│   └── frontend_dist/          # assets gerados, ignorados pelo Git
├── frontend/src/               # React, componentes, workspaces e CSS
├── scripts/                    # diagnóstico, replay, calibração e build
├── launcher/                   # launcher local Windows
├── tests/                      # suíte Python e fixture numérica de regressão
└── docs/                       # documentação e capturas sem conteúdo privado
```

`desktop_ui.py`, `alerts_ui.py`, `calibration_ui.py` e `desktop_dialogs.py` mantêm a alternativa Qt Widgets. `web/` pertence ao painel HTTP legado. `python -m camera_ai` é a entrada de câmera única e não abre o desktop multicâmera atual.

## Ciclo de edição

Siga a [instalação](installation.md) com Python editável e Node 24. Edite os fontes em `frontend/src`, execute o build e reabra o desktop para carregar os assets novos:

```powershell
Push-Location frontend
npm.cmd run typecheck
npm.cmd run build
Pop-Location
.\.venv\Scripts\python.exe -m camera_ai.desktop_dashboard
```

O comando Vite `npm.cmd run dev` existe, mas um navegador comum não contém QWebChannel. Sem um adaptador de desenvolvimento adicional, ele não é uma operação completa do sistema. Não há endpoints remotos de desenvolvimento nem servidor automático para a UI 3.0.

Não inicie duas instâncias sobre os mesmos dados. A entrada normal usa bloqueio de instância única. Encerrar e reabrir pausa o monitoramento durante esse intervalo.

## Testes e verificações existentes

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src\camera_ai
.\.venv\Scripts\python.exe -m pip check
Push-Location frontend
npm.cmd run typecheck
npm.cmd run build
npm.cmd audit --omit=dev
Pop-Location
.\.venv\Scripts\python.exe scripts\check_documentation.py
```

A suíte Python cobre estados temporais, zonas, geometria, associação/tracking, gravação, retenção, exportação, notificações simuladas, UI Qt e transporte da interface híbrida. Não depende de enviar mensagens reais nem contém vídeos privados. Um teste de regressão usa características numéricas anonimizadas, não imagens ou rótulos de treinamento de pose.

Não há ESLint, Ruff, Black ou suíte automatizada de navegador configurados como scripts do projeto. TypeScript usa checagem estrita; mantenha o estilo existente. Testes Qt precisam de ambiente com as bibliotecas gráficas disponíveis. As verificações manuais de fluxos/capturas não são apresentadas como pipeline de CI.

O validador de documentação verifica links relativos, imagens, âncoras, estrutura básica dos blocos Mermaid e padrões sensíveis na documentação. A renderização Mermaid é uma verificação separada, registrada no [relatório de publicação](publication.md). Ele não acessa nem imprime o `.env` privado.

## Replays e dados privados

Os scripts existem, mas os vídeos e manifests locais não são distribuídos:

```powershell
.\.venv\Scripts\python.exe scripts\validate_fall_clips.py --help
.\.venv\Scripts\python.exe scripts\validate_visual_tracking.py --help
.\.venv\Scripts\python.exe scripts\calibrate_fall_dataset.py --help
```

Para um replay com seus próprios arquivos autorizados:

```powershell
.\.venv\Scripts\python.exe scripts\validate_fall_clips.py --fall CAMINHO_TESTE.mp4 --activity CAMINHO_ATIVIDADE.mp4 --report logs\validacao.json
```

Resultados em exemplos usados para ajustar regras não equivalem a avaliação independente. Scripts de calibração podem ter defaults para o dataset privado original: informe fontes próprias quando a opção existir e não execute assumindo que esses dados vêm com o clone. `prepare_daily_review.py` e `inspect_camera_geometry.py` são utilitários históricos vinculados à sessão privada original; não são passos de instalação. O primeiro não implementa `--help` e pode escrever um manifest, por isso não deve ser usado como comando exploratório.

## Build e distribuição

O frontend é compilado antes do empacotamento Python. Para verificar um wheel:

```powershell
.\.venv\Scripts\python.exe -m pip wheel . --no-deps --no-build-isolation --wheel-dir build\wheels
```

`pyproject.toml` inclui os assets de `frontend_dist` se já existirem. Um wheel não instala automaticamente as dependências de `requirements.txt`, pesos ou FFmpeg. A resolução de dados por caminhos relativos aos fontes também impede considerar essa etapa um instalador portátil pronto; a instalação editável continua sendo o fluxo documentado.

O script existente `scripts/build_desktop_launcher.ps1` usa PyInstaller para criar um `.exe` **na Área de Trabalho**. Seu fonte, `launcher/ustudio_vision_launcher.py`, contém o caminho fixo da instalação original. Em outra máquina, esse caminho precisa ser adaptado antes de usar o launcher. O script pode substituir o executável homônimo: não o execute como teste de documentação ou esperando um pacote autônomo.

Não há instalador assinado, atualização automática ou release pública configurada. [Licenças e direitos de redistribuição](licensing.md) precisam ser resolvidos antes de distribuir binários, pesos ou mídia.

## Logs e depuração

Inicie pelo terminal com `python.exe` para acompanhar erros de inicialização. `logs/desktop-app.log` é o log da entrada desktop; os monitores escrevem amostras e transições em arquivos por canal. Consulte também a página Dispositivos e as métricas de Observabilidade.

Não cole logs integrais, URLs autenticadas, dumps do histórico ou capturas de ambientes privados em issues. A interface alternativa de diagnóstico é `--classic-ui`; ela mantém os mesmos dados/backend e não resolve incompatibilidade de câmera por si só.
