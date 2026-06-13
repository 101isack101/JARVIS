import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../overlay/web_dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/events': 'http://127.0.0.1:8765',
      '/state': 'http://127.0.0.1:8765',
      '/approval': 'http://127.0.0.1:8765',
      '/command': 'http://127.0.0.1:8765',
    },
  },
})
