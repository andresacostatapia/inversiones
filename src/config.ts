/**
 * Dirección del servidor propio (carpeta worker/): entrega cotizaciones y vigila las alertas.
 * En desarrollo apunta al servidor local (`npm --prefix worker run dev`).
 * Vacío = sin servidor: los precios se editan a mano y las alertas solo se revisan con la app abierta.
 */
const PUBLISHED = 'https://inversiones-cotizaciones.inversiones-cotizaciones.workers.dev'

export const QUOTES_URL: string = import.meta.env.VITE_QUOTES_URL ?? (import.meta.env.DEV ? 'http://localhost:8787' : PUBLISHED)

/** Dólar observado del Banco Central, vía mindicador.cl (se consulta directo, sin servidor propio). */
export const DOLLAR_URL = 'https://mindicador.cl/api/dolar'

/**
 * Clave PÚBLICA con la que el servidor firma las notificaciones push (la misma está en
 * worker/wrangler.jsonc). No es un secreto: solo permite al celular reconocer al remitente.
 */
export const VAPID_PUBLIC = 'BJTokTnisZsNt6_QISFhxss_T06GuPEOEal_z-8SPPoHRj7c0TVPCtbFzkzRuuFa7GqpUoISzudwyaHbYnr2nIY'
