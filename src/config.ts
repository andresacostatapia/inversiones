/**
 * Dirección del servidor de cotizaciones (carpeta worker/).
 * En desarrollo apunta al servidor local (`npm --prefix worker run dev`).
 * Vacío = sin cotizaciones automáticas: los precios se editan a mano.
 */
const PUBLISHED = 'https://inversiones-cotizaciones.inversiones-cotizaciones.workers.dev'

export const QUOTES_URL: string = import.meta.env.VITE_QUOTES_URL ?? (import.meta.env.DEV ? 'http://localhost:8787' : PUBLISHED)

/** Dólar observado del Banco Central, vía mindicador.cl (se consulta directo, sin servidor propio). */
export const DOLLAR_URL = 'https://mindicador.cl/api/dolar'
