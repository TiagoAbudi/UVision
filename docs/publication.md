# Publicação e auditoria

## Limite do repositório

O projeto a publicar é o conteúdo de **`camera-ai`**. A pasta pai contém outras automações, dependências e um `.env` compartilhado; não deve ser enviada inteira como se fosse somente UVision. O README dessas outras áreas foi preservado.

Na auditoria inicial de 19/11/2026, nem o workspace nem `camera-ai` tinham repositório `.git` inicializado. Assim, não havia histórico local a inspecionar. Uma área Git temporária dentro de `build` foi usada somente para testar as regras de ignore e enumerar candidatos. Depois, o responsável autorizou o commit local do código completo: a preparação passa a incluir a inicialização Git de `camera-ai`, revisão do índice e primeiro commit, sem push ou reescrita de histórico.

O responsável informou o remoto [TiagoAbudi/UVision](https://github.com/TiagoAbudi/UVision), com URL Git `https://github.com/TiagoAbudi/UVision.git`. A página e `git ls-remote` indicaram um repositório vazio em 19/11/2026. Esse é o destino do `origin` local; nenhum arquivo foi enviado e nenhum repositório remoto foi criado por esta preparação.

Se um histórico/remote for adicionado localmente depois, audite-o novamente: as conclusões abaixo não cobrem commits importados no futuro.

## Achados e correções

| Achado | Preparação realizada |
|---|---|
| README e exemplo continham IPs da instalação | README substituído por apresentação pública; exemplo usa rede reservada `192.0.2.0/24`, sem senha/token. |
| Gravações, datasets, calibrações e build não estavam todos cobertos pelo ignore | Regras ampliadas no projeto e proteção adicional na raiz do workspace. Nenhum dado local foi apagado. |
| Relatórios históricos de UI apontavam para imagens privadas e caminhos locais | Preservados localmente e excluídos do conjunto público. Novos guias e imagens são autocontidos. |
| Fixture de regressão incluía horários reais de um teste | Descrição e timestamps anonimizados, com intervalos e características numéricas preservados; suíte passou. Original preservado em backup privado de build. |
| Assets do bot, gravações e datasets sem direitos públicos definidos | Excluídos do conjunto publicável; nenhuma licença de dataset presumida. |
| Launcher com caminho fixo e requisitos fora do `pyproject.toml` | Limites documentados; instalação por fontes editáveis e build frontend explícito. Código funcional preservado. |
| Inicialmente sem licença autoral ou canal de segurança confirmado | Responsável escolheu MIT e relatos privados do GitHub; LICENSE criado e recurso do GitHub habilitado/verificado. Termos de terceiros preservados. |

## O que o Git deve ignorar

- `.env` e variações privadas, chaves/credenciais; `.env.example` permanece incluído.
- `recordings`, `snapshots`, `logs`, `datasets`, `runtime` e calibrações da instalação.
- Pesos `.pt`, exportações ONNX/engine e vídeos locais.
- Venv, `node_modules`, caches Python/TypeScript e metadados de build.
- `build`, `dist`, artefatos PyInstaller e frontend compilado.
- Assets privados do bot e relatórios históricos locais de UI.

`docs/images` contém somente novas capturas e a gravação GIF com ocultação prévia de conteúdo privado. Não use `git add -f` para contornar essas regras. O ignore não é uma auditoria automática de tudo que uma pessoa pode inserir em código, comentários ou novas imagens.

## Verificações executadas e alcance

As verificações usam uma lista de candidatos fornecida pelo Git com as regras reais do projeto, sem adicionar arquivos ao índice de trabalho. Foram comparados, em memória e sem imprimir os valores, as credenciais e hosts reais contra os textos candidatos. Também foram procurados blocos de chave privada e formatos de token. Dois testes usam tokens deliberadamente fictícios com transporte simulado; não coincidem com as credenciais locais.

Os 75 fontes de aplicação/frontend/testes protegidos por hashes permaneceram iguais durante esta tarefa. A única fixture alterada contém números e timestamps anonimizados; não houve alteração de serviços, modelos, parâmetros de detecção, credenciais ou configuração operacional.

Na auditoria inicial, a suíte Python passou com 178 testes após a anonimização e novamente com 178 testes na cópia dos fontes publicáveis. TypeScript, build Vite, `pip check` e resolução local dos requisitos passaram; a cópia reconstruiu o frontend com `npm ci`. Foram verificados 110 links relativos/âncoras e 29 links externos sem falhas, 11 arquivos de imagem/animação e seis blocos Mermaid renderizados com sucesso. A prévia HTML do README foi inspecionada, sem imagens quebradas ou overflow horizontal. A auditoria npm das dependências de produção não reportou vulnerabilidades. A validação documental é repetida ao atualizar a licença; as contagens de links podem mudar.

A cópia de instalação foi feita a partir dos candidatos, sem `.env`, vídeos, modelos ou calibrações privadas. Dependências Python já disponíveis foram reutilizadas nesse ensaio: ele verifica isolamento dos fontes e ausência de dependência dos dados privados, sem afirmar uma instalação completa de CUDA em um Windows recém-formatado. O exemplo carregou oito canais, sem exigir geometria privada, com Telegram desativado; uma senha fictícia foi fornecida somente no processo de teste. Nenhuma câmera foi conectada a partir dessa configuração ilustrativa.

O GIF é uma gravação real de 28 segundos a 4 FPS, com a janela protegida antes da captura. Não é montagem de telas. Imagens não demonstram precisão da IA. Nenhuma queda foi encenada nem mensagem de teste enviada nesta tarefa; operações de captura não salvaram calibrações, revisões ou preferências de desempenho.

Os resultados técnicos detalhados ficam em `build/publication-audit`, fora do Git. O comando público para repetir a validação de documentação é:

```powershell
.\.venv\Scripts\python.exe scripts\check_documentation.py
```

O script não lê o `.env` real e não substitui revisão manual de segurança. Renderização Mermaid e revisão de pixels são verificações adicionais.

## Arquivos entregues

**Documentos criados:**

| Grupo | Arquivos |
|---|---|
| Raiz | `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`, `LICENSE` (MIT escolhida pelo responsável) |
| Navegação e arquitetura | `docs/README.md`, `docs/architecture.md`, `docs/diagrams/architecture.md`, `docs/diagrams/processing-flow.md` |
| Instalação e operação | `docs/installation.md`, `docs/configuration.md`, `docs/cameras.md`, `docs/ai-detection.md`, `docs/calibration.md`, `docs/recordings.md`, `docs/alerts.md`, `docs/troubleshooting.md` |
| Desenvolvimento e publicação | `docs/development.md`, `docs/licensing.md`, `docs/publication.md` |
| Galeria | `docs/images/README.md` |

**Arquivos atualizados:** `README.md`, `.env.example`, `.gitignore` do projeto, `.gitignore` da pasta pai e `tests/fixtures/ch04-reacquired-ground-20261119.json` somente para anonimização dos metadados temporais. O validador `scripts/check_documentation.py` foi adicionado; nenhuma dependência de runtime foi introduzida.

**Imagens novas:** `dashboard.png`, `monitoring.png`, `camera-detail.png`, `recordings.png`, `playback.png`, `alerts.png`, `alert-settings.png`, `calibration.png`, `ai-performance.png`, `devices.png`, mais `overview.gif`, todas em `docs/images`. A [galeria](images/README.md) descreve cada vista e a ocultação de dados. O conjunto foi otimizado sem reduzir a resolução das capturas de 1904×1001; o GIF usa 1280×720.

Todos os guias estão preenchidos e ligados ao código atual. A licença MIT do código próprio e o canal GitHub foram definidos pelo responsável e aplicados. Isso não declara que as dependências ou uma distribuição binária combinada estejam sujeitas apenas a MIT.

## Estado das decisões e publicação

1. Licença do código próprio: MIT aprovada e adicionada em LICENSE; [escopo e dependências](licensing.md).
2. Segurança: relatos privados do GitHub habilitados e verificados; [política de segurança](../SECURITY.md).
3. Conferir o destino já informado: `TiagoAbudi/UVision`. O primeiro envio ainda não foi realizado.
4. Revisar o diff e as capturas públicas. Streams visíveis exigem material autorizado; as capturas atuais ocultam os ambientes.
5. Confirmar os termos de dependências e modelos para a distribuição pretendida. Não incluir pesos/binários/datasets como se fossem cobertos automaticamente pela licença do código próprio.

## Referência de comandos Git

Os comandos seguintes documentam a preparação local e o eventual envio. O commit local foi autorizado; o push não foi autorizado nem executado. Use a pasta `camera-ai`. Em um checkout já inicializado, revise o estado e o remote em vez de reinicializar ou substituí-lo.

```powershell
git init -b main
git status --short --untracked-files=all
git check-ignore .env runtime/events.json recordings/falls/exemplo.mp4
git add --dry-run .
```

Revise a lista antes de adicionar, valide a documentação e prepare o commit:

```powershell
.\.venv\Scripts\python.exe scripts\check_documentation.py
git add .
git diff --cached --name-only
git diff --cached --check
git diff --cached
git commit -m "feat: add UStudio Vision Control desktop application"
```

Confira especialmente imagens, fixtures, configurações novas e a ausência dos dados locais. Não compartilhe a saída do diff se encontrar segredo. Se houver remote configurado, apenas confira-o; não o substitua automaticamente. Para uma pasta nova sem remote, e somente quando o responsável autorizar a publicação:

```powershell
git remote add origin https://github.com/TiagoAbudi/UVision.git
git remote -v
git push -u origin main
```

O repositório GitHub já foi informado e existe. Esses comandos configuram o destino somente em uma pasta nova sem remote; não substitua um remote existente automaticamente. O push continua dependendo de autorização do responsável e da revisão das pendências.
