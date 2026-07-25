import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Phones hit this dev server over the LAN, so it binds 0.0.0.0 and proxies the API and
// socket through to the backend. One origin means no CORS, no mixed content, and one URL
// to explain when five people are scanning a QR code.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
  build: { chunkSizeWarningLimit: 1500 },
})
