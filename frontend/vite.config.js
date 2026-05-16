import { defineConfig, loadEnv } from 'vite' // [1]
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => { //
  // Load the .env file
  const env = loadEnv(mode, process.cwd(), ''); 

  return {
    plugins: [react()],
    server: {
      port: 3000,
      host: '0.0.0.0', // Allow Public Access
      proxy: {
        // Dynamic Proxy Routes
        '/auth':       { target: env.VITE_API_URL || 'http://127.0.0.1:10000', changeOrigin: true },
        '/api':        { target: env.VITE_API_URL || 'http://127.0.0.1:10000', changeOrigin: true },
        '/activities': { target: env.VITE_API_URL || 'http://127.0.0.1:10000', changeOrigin: true },
        // ... apply to all routes
      },
    },
  }
})