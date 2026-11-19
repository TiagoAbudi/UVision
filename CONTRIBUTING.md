# Como contribuir

Obrigado por contribuir com o UStudio Vision Control. O código próprio e a documentação usam a [licença MIT](LICENSE), escolhida pelo responsável. Dependências, modelos e outros materiais de terceiros conservam seus termos; consulte [licenciamento](docs/licensing.md) antes de reutilizar ou enviar esse material.

## Preparar o ambiente

Siga [instalação](docs/installation.md) e [desenvolvimento](docs/development.md). Use Python 3.12, Node 24 e configuração privada própria. Não copie os dados de outra instalação. A interface atual é React dentro de Qt WebEngine, com backend Python compartilhado; preserve essa fronteira ao propor mudanças.

## Relatar bugs e propor melhorias

Use as Issues de [TiagoAbudi/UVision](https://github.com/TiagoAbudi/UVision) para bugs não sensíveis e propostas. Informe:

- Comportamento esperado, observado e passos para reproduzir.
- Versões do sistema, Python, dependências e GPU/CPU, sem nomes pessoais de máquina.
- Canal/estado de conexão e métricas relevantes, substituindo IPs e nomes privados.
- Log mínimo sanitizado ou caso sintético reproduzível; não anexe o log integral.
- Captura com todos os ambientes, rostos, tokens, IDs e caminhos privados ocultados.

Uma detecção errada deve ser descrita como falso positivo/negativo observado, sem afirmar precisão global. Vídeos de pessoas vulneráveis e gravações privadas não devem ser compartilhados em issues. Vulnerabilidades seguem [SECURITY.md](SECURITY.md).

## Código e testes

Faça alterações focadas, respeitando os módulos e padrões existentes. Reutilize controles, helpers e serviços; não crie outra captura ou outro modelo por página. Parâmetros de geometria devem continuar no sistema normalizado da imagem bruta. Caixas suavizadas não podem passar a confirmar quedas.

Execute a suíte Python, checagem TypeScript, build e validador de documentação descritos no guia. Acrescente teste quando ele verificar comportamento relevante ou corrigir uma regressão; evite testes que só repitam detalhes da implementação. Para mudanças em captura/inferência/gravação, descreva validação operacional além dos testes simulados.

Não há formatter/linter adicional padronizado no repositório. Preserve o estilo de cada módulo e não reformate arquivos inteiros em uma correção pequena.

## Pull requests

Explique o problema, comportamento final, escopo, testes executados e limitações. Para UI, anexe capturas reais seguras; para detecção, descreva as condições avaliadas e a separação entre dados de ajuste e avaliação. Não apresente resultados de replay como validação clínica.

Antes de enviar, confira o diff, os caminhos novos e o conteúdo que será versionado. Exclua `.env`, runtime, calibração local, logs, vídeos, snapshots, datasets, modelos, venv e builds. Não inclua dados só porque o Git permite adicioná-los com `-f`.

Prefira commits focados com `feat:`, `fix:`, `docs:`, `test:`, `refactor:` ou `chore:`. Não altere contratos, parâmetros críticos ou dependências sem explicar a necessidade. As contribuições ao código próprio devem ser compatíveis com MIT e feitas por quem possui os direitos necessários; não há CLA ou processo formal adicional configurado.
