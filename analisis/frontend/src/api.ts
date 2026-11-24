import type {
  AlertItem,
  AlertsResponse,
  BacktestResult,
  Bar,
  ContributionResult,
  ImportResult,
  IndicatorsResponse,
  Portfolio,
  QuoteView,
  ScorePoint,
  Signal,
  Status,
  Transaction,
} from './types'
import { isNative } from './local/net'
import type { CfdCalibration, CfdDetail, CfdOverview, CfdSettings, CfdTf } from './cfdTypes'
import type { SearchResult } from './core/catalog'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

/** Modo local (app Android, o `VITE_TARGET=mobile` para probarlo en el navegador): todo se calcula en el
 * dispositivo, sin servidor. Si no, se usa la API HTTP del servidor de Python (Windows y escritorio). */
export const LOCAL_MODE = import.meta.env.VITE_TARGET === 'mobile' || isNative()

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (LOCAL_MODE) {
    const { localRequest } = await import('./local/router')
    try {
      return (await localRequest(path, init)) as T
    } catch (e) {
      throw new ApiError((e as { status?: number }).status ?? 500, (e as Error).message)
    }
  }
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
  search: (q: string) => request<{ query: string; results: SearchResult[] }>(`/search?q=${encodeURIComponent(q)}`),
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

  // Cartera
  portfolio: () => request<Portfolio>('/portfolio'),
  transactions: () => request<Transaction[]>('/transactions'),
  addTransaction: (t: NewTransaction) => request<Transaction>('/transactions', { method: 'POST', body: JSON.stringify(t) }),
  deleteTransaction: (id: number) => request<{ deleted: number }>(`/transactions/${id}`, { method: 'DELETE' }),
  importCsv: (csv: string, commit: boolean) =>
    request<ImportResult>('/transactions/import', { method: 'POST', body: JSON.stringify({ csv, commit }) }),
  contribution: (amount_clp: number) =>
    request<ContributionResult>('/contribution', { method: 'POST', body: JSON.stringify({ amount_clp }) }),

  // Copiloto CFD (solo con el servidor de Python: navegador y Windows)
  cfdOverview: (tf?: CfdTf) => request<CfdOverview>(`/cfd/overview${tf ? `?tf=${tf}` : ''}`),
  cfdDetail: (code: string, tf?: CfdTf) => request<CfdDetail>(`/cfd/assets/${encodeURIComponent(code)}${tf ? `?tf=${tf}` : ''}`),
  cfdSaveSettings: (patch: Partial<CfdSettings>) =>
    request<CfdSettings>('/cfd/settings', { method: 'PUT', body: JSON.stringify(patch) }),
  cfdCalibrate: (body: { asset: string; xtb_price?: number; at?: string; manual_offset?: number }) =>
    request<CfdCalibration>('/cfd/calibrate', { method: 'POST', body: JSON.stringify(body) }),
  cfdClearCalibration: (code: string) => request<unknown>(`/cfd/calibrate/${encodeURIComponent(code)}`, { method: 'DELETE' }),

  // Alertas
  alerts: () => request<AlertsResponse>('/alerts'),
  readAlert: (id: number) => request<unknown>(`/alerts/${id}/read`, { method: 'POST' }),
  readAllAlerts: () => request<unknown>('/alerts/read-all', { method: 'POST' }),
  testAlert: () => request<{ alert: AlertItem; sent: string[] | string | null; errors: string[]; channels: Record<string, boolean> }>(
    '/alerts/test', { method: 'POST' }),
}

export interface NewTransaction {
  symbol: string
  side: 'compra' | 'venta'
  quantity: number
  price: number
  date: string
  fee: number
  fx_rate: number | null
  note: string | null
}
