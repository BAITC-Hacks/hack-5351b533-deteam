import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd())
  return {
    plugins: [react()],
    server: {
      // Запросы фронта на /api уходят на бэкенд — CORS настраивать не нужно
      proxy: { '/api': env.VITE_BACKEND_URL || 'http://localhost:8000' },
    },
  }
})
