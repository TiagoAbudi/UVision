import {copyFileSync,writeFileSync} from 'node:fs'
const target=new URL('../../src/camera_ai/frontend_dist/',import.meta.url)
copyFileSync(new URL('../node_modules/@fontsource-variable/inter/LICENSE',import.meta.url),new URL('LICENSE-Inter.txt',target))
writeFileSync(new URL('THIRD-PARTY.txt',target),'Inter: SIL Open Font License 1.1 (LICENSE-Inter.txt).\nReact: MIT. Radix Primitives: MIT. Lucide: ISC.\nSee frontend/package-lock.json for the exact bundled versions.\n')
