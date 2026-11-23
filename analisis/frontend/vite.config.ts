import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// En desarrollo, /api se redirige al backend (uvicorn en el puerto 8000): sin CORS.
// En producción, `npm run build` deja el sitio en dist/ y FastAPI lo sirve en http://127.0.0.1:8000.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
