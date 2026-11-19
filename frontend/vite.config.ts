import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({ plugins: [react()], base: './', build: { outDir: '../src/camera_ai/frontend_dist', emptyOutDir: true, target: 'chrome110' } })
