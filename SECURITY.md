# Segurança

## Estado e contato

O canal de segurança é o **relato privado de vulnerabilidades do GitHub**, habilitado e verificado em 19/11/2026 para TiagoAbudi/UVision. Não é preciso criar outro servidor ou divulgar um e-mail pessoal. Este é um projeto experimental sem política de suporte a versões ou SLA de resposta definido.

Para relatar uma vulnerabilidade, entre no GitHub e abra **[Security → Advisories](https://github.com/TiagoAbudi/UVision/security/advisories) → Report a vulnerability**. O relato fica privado para as pessoas autorizadas enquanto o caso é avaliado. Não abra issue pública com exploração, token, vídeo privado ou dados da rede. O funcionamento do recurso está descrito na [documentação oficial do GitHub](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository).

Um relato privado deve conter versão/commit, componente, impacto, reprodução mínima sanitizada e condições necessárias, evitando credenciais reais. Bugs comuns e sugestões sem dados sensíveis podem seguir pelas Issues públicas.

## Dados que não devem ser publicados

- Arquivos `.env`, senhas de câmeras/DVR, tokens do bot e chaves privadas.
- URLs RTSP autenticadas, IDs pessoais de chat, identidade do dispositivo e topologia/IPs da instalação.
- Logs integrais, `runtime/events.json`, offsets/IDs de mensagens e imagens de alerta.
- Gravações, snapshots, datasets, características vinculadas a pessoas/horários e perfis privados de ambiente.
- Capturas com rostos, interior de residências, documentos, placas ou caminhos com nome de usuário.

O `.gitignore` reduz inclusão acidental, mas não remove arquivos já rastreados nem apaga histórico. Se um segredo for exposto, revogue/rotacione primeiro; depois avalie remoção coordenada do histórico. Não reescreva branches ou histórico sem autorização do responsável.

## Operação local

Credenciais e vídeos são armazenados em arquivos locais sem criptografia própria do aplicativo. Restrinja permissões da pasta e do computador, use conta de câmera com acesso mínimo, backups protegidos e mecanismos de criptografia do sistema quando necessários.

Mantenha câmeras/DVR em rede administrada e, quando possível, segmentada. RTSP/CGI/ONVIF usados na integração não garantem criptografia de transporte. Não exponha essas portas ou o painel HTTP legado diretamente à internet. A UI desktop não precisa de servidor HTTP nem de encaminhamento de portas.

Telegram envia dados a um serviço externo e aos chats configurados. Use bot dedicado, controle membros dos grupos e trate token/IDs como privados. Confirmar recebimento não é uma decisão clínica ou encerramento garantido de uma emergência.

Baixe pesos e ferramentas de fontes oficiais. Modelos `.pt`, pacotes e binários desconhecidos não são dados inofensivos. Verifique as licenças e a procedência antes de redistribuir ou executar.

## Verificações antes de publicar

Siga [publicação e auditoria](docs/publication.md). Não use `git add -f`, não publique a pasta inteira do workspace e não transforme os relatórios locais de UI em imagens públicas sem revisão. O histórico Git e o remote deverão ser auditados novamente se forem criados ou importados depois desta preparação.
