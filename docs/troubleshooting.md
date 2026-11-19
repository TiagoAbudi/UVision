# Troubleshooting

Diagnostique por camada: equipamento/rede, captura, inferência, evento, arquivo e entrega. Um clipe salvo não comprova entrega Telegram; uma câmera online não comprova inferência recente. Os endereços e arquivos reais devem permanecer privados.

## Câmera offline ou sem imagem

**Sintoma:** canal offline, equipamento sem resposta, stream indisponível ou quadros parados.

**Possíveis causas:** câmera desligada, host alterado, porta bloqueada, credenciais rejeitadas, caminho RTSP incompatível, codec/limite de conexões do DVR ou stream congelado.

**Diagnóstico:** veja estado, idade do quadro e reconexões em Dispositivos. Verifique alimentação e visualização no aplicativo do equipamento. Teste a porta 554 do host administrado e valide usuário/permissões localmente; nunca publique a URL autenticada.

**Ação:** corrija os dados no `.env` privado, confira o caminho efetivamente suportado e reinicie a aplicação quando mudar configuração de câmera. Reconectar reinicia somente o canal. Localizar DVR busca a identidade já pareada; não substitui qualquer DVR indisponível por outro detectado. Consulte [câmeras](cameras.md) e o log desktop.

## Vídeo fluido, mas caixas ou decisões atrasadas

**Sintoma:** FPS de vídeo maior que FPS de análise, latência alta ou avisos de inferência lenta.

**Possíveis causas:** CPU/GPU disputada, modelo/resolução caros, aquecimento, excesso de streams ou pouca memória.

**Diagnóstico:** em Observabilidade compare FPS por câmera, média/p95 e último lote; em Dispositivos compare idade do quadro com idade do resultado. O log registra inferência lenta e descartes de evidência antiga. Frames com mais de um segundo não contam para confirmar queda.

**Ação:** aguarde aquecimento, confirme GPU, reduza carga concorrente e avalie Equilibrado/Econômico explicitamente. Não reduza parâmetros sem revisar casos de atividade normal e queda. Alterar `DVR_MOSAIC_FPS` não regula os canvas da UI 3.0.

## GPU não detectada

**Sintoma:** dispositivo CPU, CUDA indisponível ou erro ao selecionar índice CUDA.

**Diagnóstico:** execute `scripts/check_environment.py` e verifique `torch.cuda.is_available()`, versão instalada, driver e GPU. O arquivo de requisitos fixa a distribuição CUDA 12.8; outro Python/pip pode estar sendo usado fora do venv.

**Ação:** use os executáveis `.venv\Scripts\python.exe`/pip do mesmo ambiente e siga as orientações oficiais do [PyTorch](https://pytorch.org/get-started/locally/). CPU é possível no código, mas não é uma garantia de operação satisfatória para nove câmeras.

## Suspeita sem clipe, ou falso evento de queda

**Sintoma:** `possible_fall` desaparece sem MP4, ou uma atividade normal chega a `alert_pending`.

**Diagnóstico:** suspeita sem confirmação é descartada por projeto. Confira pose recente, tempo contínuo, perspectiva, zonas de repouso/chão e orientação. Uma perda de pose reinicia a evidência; geometria de outra câmera pode provocar erros.

**Ação:** revise e calibre o próprio enquadramento; classifique/preserve exemplos relevantes. Não desative o filtro de animais ou diminua limiares sem avaliação. Para registrar, o segmento precisa ter alcançado `alert_pending`; o arquivo costuma ser concluído após o retorno ao normal e o período posterior. Veja [IA](ai-detection.md) e [gravações](recordings.md).

## Falha ao salvar ou reproduzir vídeo

**Sintoma:** não aparece MP4 concluído, arquivo não abre ou reprodução fica sem frame.

**Possíveis causas:** falta de espaço/permissão, codec local indisponível, primeiro frame inválido, gravação ainda temporária ou arquivo danificado.

**Diagnóstico:** confira `recordings/falls`, o log do gravador e espaço livre. Abra o original por um player local autorizado para separar problema do arquivo de problema da interface.

**Ação:** preserve o arquivo original antes de qualquer tentativa de reparo. Corrija disco/permissões e confirme o codec OpenCV. Não apague o histórico de eventos como primeira tentativa. Não há cota automática de disco para protegidos/cache/logs.

## Alertas não chegam

**Sintoma:** clipe local existe, mas nenhum aviso no bot.

**Diagnóstico:** confira envio habilitado, token configurado, chat iniciado com `/start`, destinatários corretos, canais selecionados e se o evento realmente alcançou `alert_pending`. Em shadow mode, é necessário permitir os testes de calibração. O histórico mostra entrega e falhas.

**Ação:** teste a comunicação pela página Alertas. Verifique acesso HTTPS/DNS, bloqueio do bot pelo destinatário, permissões no grupo e validade do token. Para buscar chat, desative e salve primeiro. Um teste de mensagem bem-sucedido não testa a conversão/upload do clipe. [Fluxo completo](alerts.md).

## Vídeo verde no celular ou entrega só da imagem

**Diagnóstico:** confira FFmpeg no PATH e encoder `libx264`; o original local usa `mp4v` e a cópia móvel precisa ser H.264/yuv420p. O log e o histórico registram falha de conversão, timeout ou envio. O aplicativo limita o anexo a 48 MiB.

**Ação:** valide o FFmpeg da instalação e o vídeo original. Preserve o original; a cópia fica em `runtime/telegram-videos`. Não reenvie arquivos privados como teste público. Uma falha remota pode persistir mesmo com conversão correta.

## Botão de recebimento não funciona ou chat não fica limpo

**Possíveis causas:** bot em uso por outro processo/webhook, aplicação parada, chat não autorizado, callback expirado, mensagem antiga ou exclusão negada pela API.

**Diagnóstico:** veja o histórico local; confirme que existe um único consumidor do bot e que o aplicativo está rodando. O worker de recebimentos é separado do upload. A limpeza depende dos IDs conhecidos e das restrições do Telegram.

**Ação:** use bot dedicado e revise sua configuração externa. Não há remoção automática de webhook nem varredura destrutiva do chat. Mensagens fora das condições de exclusão podem precisar de remoção manual. Recebimento confirmado não significa bem-estar verificado.

## Calibração não salva ou ficou inválida

**Diagnóstico:** câmera precisa estar online e habilitada para quedas. Polígonos exigem área útil; pontos ficam entre 0 e 1; verticais não podem ser curtas/degeneradas. Um movimento PTZ exige novas referências.

**Ação:** ajuste pontos e duas verticais físicas separadas, confira canal/rotação e revise antes de aplicar. Não edite `viewChanged` para contornar a recalibração; não copie o perfil de outro ambiente. Preserve os arquivos existentes antes de recuperação manual.

## Erro de inicialização ou interface vazia

**Diagnóstico:** confira Python 3.12, requisitos, instalação editável e `src/camera_ai/frontend_dist/index.html`. Veja `logs/desktop-app.log`; `--self-test` cobre só parte da configuração. Um perfil geométrico configurado, mas ausente, impede a inicialização correta.

**Ação:** execute `npm.cmd ci`/`npm.cmd run build` em `frontend`; numa instalação nova mantenha `FALL_GEOMETRY_PROFILE=` vazio. Use `--classic-ui` como diagnóstico explícito, não como substituto de build/configuração correta. O navegador comum não tem o transporte Qt da UI.

## Portabilidade e launcher

**Sintoma:** `.exe` procura Python em outra pasta.

**Causa:** o launcher existente tem caminho fixo e não inclui o ambiente do projeto.

**Ação:** execute pelo módulo Python conforme [instalação](installation.md). Revise o caminho do fonte do launcher antes de reconstruí-lo; o script existente escreve na Área de Trabalho e pode substituir um arquivo homônimo.
