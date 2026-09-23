import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd())
  // 127.0.0.1, а не localhost: мок-сервер слушает только IPv4, а localhost на Windows может уйти в ::1
  const backend = env.VITE_BACKEND_URL || 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    server: {
      // REST и вебсокеты идут на бэкенд (или мок-сервер) — CORS не нужен
      proxy: {
        '/api': backend,
        '/ws': { target: backend, ws: true },
      },
    },
  }
})
