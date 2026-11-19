# Changelog

Registro de alterações verificadas nos arquivos atuais. Não existe histórico Git ou sequência de releases públicas disponível nesta instalação; versões antigas não foram reconstruídas por suposição. Organização inspirada em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## Não publicado — 2026-11-19

### Documentação e preparação pública

- Licença MIT aprovada para código próprio/documentação, com LICENSE e metadados Python/frontend; termos de terceiros preservados.
- Relatos privados de vulnerabilidades habilitados e verificados no GitHub; política de segurança aponta para o canal do repositório.
- Repositório identificado pelo responsável como `https://github.com/TiagoAbudi/UVision.git`; guias atualizados sem configurar remote ou enviar arquivos.
- README em português, índice e guias de instalação, configuração, câmeras, IA, calibração, gravações, alertas, arquitetura, desenvolvimento e troubleshooting.
- Diagramas Mermaid, capturas atuais da aplicação com ocultação prévia de conteúdo privado e galeria contextualizada.
- Orientações de contribuição, segurança, publicação e licenciamento, atualizadas após as escolhas explícitas do responsável.
- `.env.example` com hosts reservados para documentação, alertas desativados e sem referência obrigatória a calibrações privadas.
- `.gitignore` ampliado para dados privados, pesos, caches e artefatos; dados locais preservados.

### Estado funcional verificado nos fontes

- Interface identificada como Vision Control 3.0 em React/TypeScript embarcado no Qt WebEngine, com backend Python compartilhado e transporte local QWebChannel/esquema `uvision`.
- Monitoramento, eventos, dispositivos, calibração, alertas, observabilidade e informações da estação implementados.
- Detecção de pose e verificação multiclasse, monitores temporais por pessoa/canal, clipes confirmados, retenção com proteção e integração Telegram persistida.

Esses itens descrevem o estado encontrado; não atribuem datas de introdução não comprovadas. O frontend declara versão 3.0.0 e o pacote Python 0.1.0. Nenhuma tag/release foi criada por esta documentação.
