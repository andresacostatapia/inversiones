import { useCallback, useEffect, useRef, useState } from 'react'

export interface Polled<T> {
  data: T | null
  error: string | null
  loading: boolean
  updatedAt: Date | null
  reload: () => Promise<void>
}

/**
 * Pide `fn` al montar, cuando cambian `deps` y cada `intervalMs` (0 = sin repetir).
 * Si una actualización falla se conserva el último dato bueno y se informa el error.
 * Con la pestaña oculta no se consulta, para no gastar cupo de las APIs.
 */
export function usePolling<T>(fn: () => Promise<T>, intervalMs: number, deps: unknown[]): Polled<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const fnRef = useRef(fn)
  fnRef.current = fn
  const seq = useRef(0)

  const reload = useCallback(async () => {
    const my = ++seq.current
    setLoading(true)
    try {
      const d = await fnRef.current()
      if (my !== seq.current) return // llegó una respuesta más nueva
      setData(d)
      setError(null)
      setUpdatedAt(new Date())
    } catch (e) {
      if (my === seq.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (my === seq.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    setData(null)
    setError(null)
    reload()
    if (!intervalMs) return
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') reload()
    }, intervalMs)
    const onVisible = () => document.visibilityState === 'visible' && reload()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { data, error, loading, updatedAt, reload }
}

export type Theme = 'light' | 'dark' | 'system'

export function useThemeSetting(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem('theme') as Theme) || 'system'
    } catch {
      return 'system'
    }
  })
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('theme', theme)
    } catch {
      /* almacenamiento bloqueado: no pasa nada */
    }
  }, [theme])
  return [theme, setTheme]
}

const CHART_TOKENS = [
  'surface', 'ink', 'muted', 'line', 'grid', 'gain', 'loss',
  'series-1', 'series-2', 'series-3', 'series-4', 'series-5', 'band', 'div-pos', 'div-neg', 'div-pos-fill', 'div-neg-fill',
] as const
export type ChartColors = Record<(typeof CHART_TOKENS)[number], string>

function readColors(): ChartColors {
  const cs = getComputedStyle(document.documentElement)
  return Object.fromEntries(CHART_TOKENS.map((t) => [t, cs.getPropertyValue(`--${t}`).trim()])) as ChartColors
}

/** Colores de los gráficos leídos de las variables CSS; se actualizan al cambiar el tema. */
export function useChartColors(): ChartColors {
  const [colors, setColors] = useState<ChartColors>(readColors)
  useEffect(() => {
    const update = () =>
      setColors((prev) => {
        const next = readColors()
        return Object.keys(next).every((k) => next[k as keyof ChartColors] === prev[k as keyof ChartColors]) ? prev : next
      })
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    mq.addEventListener('change', update)
    const obs = new MutationObserver(update)
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    update() // el tema guardado pudo aplicarse antes de que este efecto empezara a escuchar
    return () => {
      mq.removeEventListener('change', update)
      obs.disconnect()
    }
  }, [])
  return colors
}
