import react from '@vitejs/plugin-react'
import { HttpsProxyAgent } from 'https-proxy-agent'
import { defineConfig, type ProxyOptions } from 'vite'

// En desarrollo, /api se redirige al backend (uvicorn en el puerto 8000): sin CORS.
// En producción, `npm run build` deja el sitio en dist/ y FastAPI lo sirve en http://127.0.0.1:8000.
//
// Modo móvil (VITE_TARGET=mobile, el mismo código que la app Android): el navegador consulta Yahoo y mindicador
// directo; como el navegador bloquea esas respuestas (CORS), en desarrollo pasan por /proxy/... En Android no hace
// falta: Capacitor usa HTTP nativo.
const UA = 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36'
// En redes que salen a internet por un proxy (HTTPS_PROXY), el proxy de desarrollo también pasa por él.
const outbound = process.env.HTTPS_PROXY ?? process.env.https_proxy
const agent = outbound ? new HttpsProxyAgent(outbound) : undefined
const external = (target: string, prefix: string): ProxyOptions => ({
  target,
  changeOrigin: true,
  agent,
  rewrite: (p) => p.replace(prefix, ''),
  headers: { 'User-Agent': UA },
})
const proxy = {
  '/api': 'http://127.0.0.1:8000',
  '/proxy/yahoo': external('https://query1.finance.yahoo.com', '/proxy/yahoo'),
  '/proxy/mindicador': external('https://mindicador.cl', '/proxy/mindicador'),
}

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', proxy },
  preview: { host: '127.0.0.1', proxy },
})
