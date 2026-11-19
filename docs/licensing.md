# Licenciamento

## Código próprio: MIT

Em 19/11/2026, o responsável escolheu MIT para o código próprio e a documentação. O texto está em [LICENSE](../LICENSE), com copyright de TiagoAbudi. Os metadados Python e frontend identificam essa licença; ela abrange as contribuições originais do projeto, sem relicenciar dependências, modelos ou outros materiais de terceiros.

A MIT permite uso, modificação e redistribuição do material abrangido, mantendo os avisos exigidos, e contém exclusão de garantias. O texto completo, e não este resumo, define as condições. Este documento não certifica a conformidade jurídica de uma distribuição combinada.

## Aplicação integrada e dependências

**MIT para o código próprio não transforma o pipeline inteiro em MIT.** O projeto importa Ultralytics diretamente e usa seus modelos. Na alternativa comunitária, as obrigações AGPL-3.0 aplicáveis à obra combinada precisam ser atendidas; para uma distribuição sob outros termos, deve-se avaliar licença empresarial ou uma integração alternativa adequada. Não foi adquirida licença empresarial nem substituído o pipeline nesta tarefa.

Ao distribuir a aplicação integrada, não a apresente como um conjunto sujeito apenas a MIT. A [política oficial Ultralytics](https://www.ultralytics.com/license) distingue AGPL-3.0 e licença empresarial. Qt, codecs e demais bibliotecas mantêm também suas condições. Excluir pesos do Git não elimina as obrigações decorrentes da integração.

A escolha de MIT está concluída. O responsável escolheu os relatos privados do GitHub como canal de segurança; consulte [SECURITY.md](../SECURITY.md) para o estado operacional. Termos de dependências e requisitos de uma distribuição binária devem continuar sendo revisados conforme o uso pretendido.

## Dependências, modelos e recursos

| Componente | Termos identificados | Consequência para esta preparação |
|---|---|---|
| Ultralytics e pesos YOLO26 | AGPL-3.0 ou alternativa empresarial, conforme a distribuição/contratação | Pesos excluídos do Git; download oficial separado. A ausência dos pesos no repositório não elimina a necessidade de revisar a integração. |
| PySide6 / Qt | LGPL-3.0/GPL ou licença comercial, com avisos adicionais | Não redistribuir o runtime como se fosse código próprio. [Termos Qt for Python](https://doc.qt.io/qtforpython-6/). |
| Qt WebEngine / Chromium | Termos Qt e múltiplas licenças de terceiros | Uma distribuição empacotada exige inventário/avisos próprios. [Licenciamento WebEngine](https://doc.qt.io/qtforpython-6/overviews/qtwebengine-licensing.html). |
| FFmpeg externo | Depende da configuração; `libx264` pode implicar GPL | O build local declara GPL e libx264; não foi adicionado ao repositório. [Condições FFmpeg](https://www.ffmpeg.org/legal.html). |
| PyInstaller | GPL com exceção para bundles e arquivos específicos Apache-2.0 | A exceção não substitui obrigações das bibliotecas empacotadas. [Licença oficial](https://pyinstaller.org/en/stable/license.html). |
| React, Radix, Vite | MIT nos pacotes consultados | Manter avisos quando redistribuir material abrangido por esses termos. |
| TypeScript | Apache-2.0 no pacote consultado | Preservar os termos aplicáveis à ferramenta e seus componentes. |
| Lucide | ISC | Ícones fazem parte do frontend; manter licença/atribuição aplicáveis. |
| Inter | SIL Open Font License 1.1 | Fonte local; o build copia a licença para os assets. |
| PyTorch / torchvision / python-dotenv | BSD nos metadados consultados | Preservar avisos conforme a distribuição. |
| OpenCV | Apache-2.0; o wheel também pode conter componentes com termos próprios | A licença do pacote não resume todos os codecs/bibliotecas do wheel. |
| pytest | MIT | Ferramenta de testes; não é uma licença do projeto. |

O [inventário de terceiros](../THIRD_PARTY_NOTICES.md) identifica fontes e alcance da conferência. Não foi emitida uma declaração de conformidade de todas as dependências transitivas ou de um instalador final.

## Datasets, imagens e testes

`datasets`, gravações, snapshots, calibrações e relatórios privados não têm autorização/licença pública identificada e foram excluídos da publicação. Não publique vídeos de pessoas ou ambientes apenas por estarem no disco local.

As capturas novas mostram a interface real com vídeos, miniaturas, IPs, IDs e dados privados ocultados antes do salvamento. Não incluem imagens de pessoas nem do interior dos ambientes. Não se atribui licença de dataset a essas imagens.

O teste de regressão distribuído contém somente características numéricas, com timestamps e descrição de origem anonimizados, preservando os intervalos do teste; não contém imagem, identidade ou keypoints de uma pessoa. Os arquivos de teste próprios distribuídos integram o material sob MIT, respeitados os direitos de terceiros.

Os arquivos gráficos locais do bot e prompts associados foram mantidos fora do conjunto publicável: sua presença não foi tomada como autorização irrestrita de redistribuição. A licença MIT de copyright não concede automaticamente direitos de marca sobre o nome e a identidade do produto.
