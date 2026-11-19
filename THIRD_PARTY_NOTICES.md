# Componentes de terceiros

Inventário inicial para revisão de publicação, em 19/11/2026. O código próprio usa [MIT](LICENSE); este arquivo não substitui as licenças originais dos componentes abaixo nem os relicencia como MIT. Versões Python diretas vêm de [requirements.txt](requirements.txt); versões frontend e dependências transitivas resolvidas vêm de [package-lock.json](frontend/package-lock.json).

| Componente direto | Versão fixada | Licença identificada / referência |
|---|---|---|
| torch | 2.11.0+cu128 | BSD-3-Clause, metadados e arquivos da distribuição instalada; [fontes](https://github.com/pytorch/pytorch) |
| torchvision | 0.26.0+cu128 | BSD, metadados instalados; [fontes](https://github.com/pytorch/vision) |
| ultralytics | 8.4.189 | AGPL-3.0, com alternativa empresarial; [termos oficiais](https://www.ultralytics.com/license) |
| opencv-python | 5.0.0.93 | Apache-2.0 e avisos de componentes empacotados; [distribuição](https://github.com/opencv/opencv-python) |
| python-dotenv | 1.2.4 | BSD-3-Clause; [fontes](https://github.com/theskumar/python-dotenv) |
| pytest | 9.1.1 | MIT; [fontes](https://github.com/pytest-dev/pytest) |
| pyinstaller | 6.22.3 | GPL com exceção e partes Apache-2.0; [licença](https://pyinstaller.org/en/stable/license.html) |
| PySide6 | 6.12.0 | Alternativas LGPL/GPL/comercial; [Qt for Python](https://doc.qt.io/qtforpython-6/) |
| react / react-dom | 19.3.1 | MIT; [fontes](https://github.com/facebook/react) |
| @radix-ui/react-* | Dialog 1.3.1, Dropdown Menu 2.2.1, Select 2.4.1, Switch/Tooltip 1.4.1 | MIT; [fontes](https://github.com/radix-ui/primitives) |
| lucide-react | 1.76.0 | ISC; [fontes](https://github.com/lucide-icons/lucide) |
| @fontsource-variable/inter | 5.3.0 | OFL-1.1 para a fonte; [Inter](https://github.com/rsms/inter) |
| typescript | 7.0.2 | Apache-2.0, conforme o lockfile; [fontes](https://github.com/microsoft/TypeScript) |
| vite / @vitejs/plugin-react | 8.3.8 / 6.3.0 | MIT; [Vite](https://github.com/vitejs/vite), [plugin](https://github.com/vitejs/vite-plugin-react) |
| @types/node, @types/react, @types/react-dom | conforme lockfile | MIT nos pacotes consultados; [DefinitelyTyped](https://github.com/DefinitelyTyped/DefinitelyTyped) |

FFmpeg é uma ferramenta externa, não vendorizada: a instalação usada na auditoria reporta 9.0.2, `--enable-gpl`, `--enable-version3` e `libx264`. Confira os termos do binário efetivamente obtido e [as condições oficiais](https://www.ffmpeg.org/legal.html). Componentes CUDA, Qt/Chromium e bibliotecas/encoders dentro de wheels têm avisos adicionais que precisam acompanhar uma distribuição binária conforme seus termos.

O build frontend copia a licença Inter e um aviso resumido para `frontend_dist`. Isso não é um inventário completo de todos os componentes de um bundle/instalador futuro. Os fontes deste repositório não incluem `node_modules`, venv, binários externos, pesos ou os assets compilados. Consulte [licenciamento](docs/licensing.md) antes de criar uma distribuição.
