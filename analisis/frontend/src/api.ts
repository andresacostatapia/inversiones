import type { BacktestResult, Bar, IndicatorsResponse, QuoteView, ScorePoint, Signal, Status } from './types'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    })
  } catch {
    throw new ApiError(0, 'No hay conexión con el servidor. ¿Está corriendo uvicorn?')
  }
  if (!res.ok) {
    let detail = `Error ${res.status}`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join('; ')
    } catch {
      /* respuesta sin JSON */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export type Period = '3m' | '6m' | '1y' | '2y' | '5y' | '10y'

export const api = {
  status: () => request<Status>('/status'),
  watchlist: () => request<QuoteView[]>('/watchlist'),
  addSymbol: (symbol: string) => request<QuoteView>('/watchlist', { method: 'POST', body: JSON.stringify({ symbol }) }),
  removeSymbol: (symbol: string) => request<{ removed: string }>(`/watchlist/${encodeURIComponent(symbol)}`, { method: 'DELETE' }),
  fx: () => request<QuoteView>('/fx'),
  signals: () => request<{ disclaimer: string; signals: Signal[] }>('/signals'),
  signal: (symbol: string) => request<Signal & { disclaimer: string }>(`/signals/${encodeURIComponent(symbol)}`),
  history: (symbol: string, period: Period) =>
    request<{ symbol: string; bars: Bar[] }>(`/instruments/${encodeURIComponent(symbol)}/history?period=${period}`),
  indicators: (symbol: string) => request<IndicatorsResponse>(`/instruments/${encodeURIComponent(symbol)}/indicators`),
  scoreHistory: (symbol: string, period: Period) =>
    request<{ points: ScorePoint[] }>(`/signals/${encodeURIComponent(symbol)}/history?period=${period}`),
  backtest: (body: { symbol: string; years: number; commission_pct: number }) =>
    request<BacktestResult>('/backtest', { method: 'POST', body: JSON.stringify(body) }),
  refresh: () => request<unknown>('/refresh', { method: 'POST' }),
}
