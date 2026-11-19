# Instalação

## Escopo validado

O desktop foi executado em Windows 11 x64, Python 3.12.10, Node.js 24.15.0, npm 11.12.1 e RTX 5060. O pacote Python restringe a versão a `>=3.12,<3.13`. Use Node 24 para repetir o build verificado. Node só é necessário no desenvolvimento/build da interface.

`requirements.txt` fixa PyTorch/torchvision com CUDA 12.8 e índice adicional oficial do PyTorch. Uma GPU NVIDIA e driver compatível são necessários para essa aceleração. O código oferece `YOLO_DEVICE=cpu`, mas nove câmeras em CPU e outras plataformas não têm desempenho validado. Não há instalação Raspberry Pi/ARM homologada nem dependências CPU-only separadas.

Instale Git, Python 3.12 x64, Node.js 24 e, para envio de vídeos, um FFmpeg com `libx264` disponível no `PATH`. Obtenha Python/Node e drivers de seus distribuidores oficiais; confira versões antes de iniciar. O FFmpeg externo não é instalado pelo pip.

## 1. Obter os fontes

O repositório informado pelo responsável é [TiagoAbudi/UVision](https://github.com/TiagoAbudi/UVision). O diretório publicável é `camera-ai`, independente das automações irmãs do workspace original. Na verificação de 19/11/2026, o remoto ainda estava vazio; as instruções abaixo se aplicam depois do primeiro envio dos fontes.

```powershell
New-Item -ItemType Directory -Path UStudio -Force
Set-Location UStudio
git clone https://github.com/TiagoAbudi/UVision.git camera-ai
Set-Location camera-ai
```

Mantenha o nome e a relação de diretórios abaixo. A resolução atual de arquivos depende da localização dos fontes, por isso a instalação recomendada é editável:

```text
UStudio/
├── .env                 # privado; configura DVR e Telegram
└── camera-ai/
    ├── .env.example
    ├── src/
    ├── frontend/
    └── ...
```

## 2. Instalar Python e dependências

Na pasta `camera-ai`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\check_environment.py
```

O projeto não declara as dependências de runtime em `pyproject.toml`: instalar apenas `-e .` é insuficiente. Instale primeiro `requirements.txt`. O uso do executável do venv evita depender da ativação PowerShell e de sua política de execução.

Para conferir a GPU:

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available()); print(torch.version.cuda)"
```

O segundo resultado representa o runtime usado pelo PyTorch; instalar um CUDA Toolkit avulso não garante que o driver ou a distribuição Python estejam corretos. Confira a [instalação oficial do PyTorch](https://pytorch.org/get-started/locally/) e o diagnóstico local antes de trocar pacotes.

## 3. Construir a interface

```powershell
Push-Location frontend
npm.cmd ci
npm.cmd run build
Pop-Location
```

O build inclui checagem TypeScript e gera `src/camera_ai/frontend_dist`, com JavaScript, CSS e fonte Inter local. Esses arquivos são gerados e ignorados pelo Git. O aplicativo informa erro se `index.html` não existir. Não execute `npm run dev` para operar o sistema: o frontend depende do transporte Qt, indisponível em um navegador comum.

## 4. Criar a configuração privada

```powershell
if (Test-Path -LiteralPath '..\.env') {
    throw 'Ja existe um .env na pasta pai. Preserve-o e revise a configuracao existente.'
}
Copy-Item -LiteralPath '.env.example' -Destination '..\.env'
```

Edite `../.env` localmente. Substitua `DVR_HOST` pelo host real e preencha `DVR_USER`/`DVR_PASSWORD`. Os endereços `192.0.2.x` do exemplo são reservados para documentação, não dispositivos acessíveis. Deixe `EXTRA_CAMERA_HOST` vazio se não houver nona câmera compatível.

Mantenha `FALL_GEOMETRY_PROFILE=` vazio até calibrar a sua instalação. Não copie a geometria de outra câmera. Comece com `FALL_SHADOW_MODE=true`, `TELEGRAM_ENABLED=false` e sem credenciais Telegram. Veja [precedência e variáveis](configuration.md).

## 5. Obter modelos oficiais

Os pesos não são incluídos. Revise primeiro os [termos Ultralytics](https://www.ultralytics.com/license) e a [documentação YOLO26](https://docs.ultralytics.com/models/yolo26/). Após aceitar as condições aplicáveis, o carregamento por nome oficial baixa os modelos disponíveis na distribuição:

```powershell
.\.venv\Scripts\python.exe -c "from ultralytics import YOLO; YOLO('yolo26s-pose.pt'); YOLO('yolo26s.pt')"
```

Execute na pasta `camera-ai`, onde os arquivos devem ficar. Não baixe `.pt` de fontes desconhecidas. O segundo modelo deve estar junto ao de pose: o verificador multiclasse usa o nome `yolo26s.pt` nessa pasta. Os modelos `yolo26n.pt`/`yolo26n-cls.pt` encontrados na instalação experimental não são necessários para esta configuração.

Configure `YOLO_MODEL=yolo26s-pose.pt`. O default interno ainda é `yolo26n.pt`, que é um detector de caixas e não fornece os pontos corporais esperados pela análise de quedas. Não confunda default de código com configuração recomendada.

## 6. Verificar e iniciar

```powershell
ffmpeg -version
ffmpeg -hide_banner -encoders | Select-String libx264
.\.venv\Scripts\python.exe -m camera_ai.desktop_dashboard --self-test
.\.venv\Scripts\python.exe -m camera_ai.desktop_dashboard
```

`--self-test` valida existência do modelo principal e configuração; **não** prova conexão RTSP, precisão da IA, funcionamento do codec externo ou entrega Telegram. Na interface, confirme canais online, GPU pronta e frames recentes. A primeira carga pode incluir aquecimento da GPU.

Calibre antes de habilitar alertas. O primeiro uso cria os diretórios necessários; não é preciso baixar datasets privados ou copiar a pasta `runtime` de outra instalação.

## Distribuição e verificações desta documentação

O launcher PyInstaller atual contém caminho fixo de uma instalação Windows. Não é um executável portátil nem inclui venv, pesos, FFmpeg e dependências. A instalação por fontes acima é o caminho documentado para outro desenvolvedor. Veja [build e distribuição](development.md#build-e-distribuição).

As versões, importações, build frontend e suíte Python foram verificadas no ambiente existente. A auditoria também usa uma cópia limpa dos arquivos publicáveis, sem `.env`, pesos ou dados privados, para testar configuração, empacotamento e testes com dependências já disponíveis. Isso não equivale a baixar toda a distribuição CUDA novamente em um Windows recém-instalado. O [relatório de publicação](publication.md) registra o alcance exato dessas verificações.
