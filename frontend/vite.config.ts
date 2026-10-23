import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const api = process.env.AETHER_API_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': api, '/healthz': api, '/metrics': api },
  },
})
