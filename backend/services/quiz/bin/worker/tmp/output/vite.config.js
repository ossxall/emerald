import { defineConfig } from 'vite'

export default defineConfig({
  server: {
    host: '0.0.0.0',
    allowedHosts: [
      'overcome-unveiled-semifinal.ngrok-free.dev'
    ]
  }
})