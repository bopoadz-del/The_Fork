import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

declare const process: { env: Record<string, string | undefined> }

export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_BASE || '/dashboard/',
  build: {
    outDir: 'dist',
  },
  server: {
    proxy: {
      '/v1': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/upload': 'http://localhost:8000',
      '/execute': 'http://localhost:8000',
      '/chain': 'http://localhost:8000',
      '/chat': 'http://localhost:8000',
    },
  },
})
