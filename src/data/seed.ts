// Datos de EJEMPLO para el prototipo. Precios y operaciones son inventados.
import { fingerprint } from '../lib/csv'
import type { AlertEvent, AlertRule, Asset, Broker, NewTrade, Quote, Settings, Trade } from '../types'

export const seedAssets: Asset[] = [
  { id: 'AAPL', name: 'Apple Inc.', type: 'accion', exchange: 'NASDAQ', currency: 'USD' },
  { id: 'NVDA', name: 'NVIDIA Corp.', type: 'accion', exchange: 'NASDAQ', currency: 'USD' },
  { id: 'VOO', name: 'Vanguard S&P 500 ETF', type: 'etf', exchange: 'NYSE', currency: 'USD' },
  { id: 'SQM-B', name: 'SQM serie B', type: 'accion', exchange: 'Bolsa de Santiago', currency: 'CLP' },
  { id: 'CHILE', name: 'Banco de Chile', type: 'accion', exchange: 'Bolsa de Santiago', currency: 'CLP' },
  { id: 'FALABELLA', name: 'Falabella S.A.', type: 'accion', exchange: 'Bolsa de Santiago', currency: 'CLP' },
  { id: 'IPSA', name: 'S&P IPSA', type: 'indice', exchange: 'Bolsa de Santiago', currency: 'CLP' },
  { id: 'USDCLP', name: 'Dólar / peso chileno', type: 'divisa', exchange: 'FX', currency: 'CLP' },
]

export const brokers: Broker[] = [
  { id: 'xtb', name: 'XTB' },
  { id: 'zesty', name: 'Zesty' },
  { id: 'racional', name: 'Racional' },
]

const q = (assetId: string, price: number, prevClose: number): Quote => ({
  assetId,
  price,
  prevClose,
  asOf: '2026-11-13T16:00:00',
})

export const seedQuotes: Record<string, Quote> = Object.fromEntries(
  [
    q('AAPL', 262.4, 259.85),
    q('NVDA', 171.2, 174.05),
    q('VOO', 628.1, 625.3),
    q('SQM-B', 41250, 40810),
    q('CHILE', 152.3, 151.9),
    q('FALABELLA', 5480, 5525),
    q('IPSA', 9120.5, 9084.2),
    q('USDCLP', 942.5, 946.1),
  ].map((x) => [x.assetId, x]),
)

const t = (
  date: string,
  assetId: string,
  side: Trade['side'],
  quantity: number,
  price: number,
  fee: number,
  currency: Trade['currency'],
  fxRate: number,
  brokerId: string,
  note = '',
): NewTrade => ({ date, assetId, side, quantity, price, fee, tax: 0, currency, fxRate, brokerId, note })

export const seedTrades: Trade[] = [
  t('2026-01-12', 'VOO', 'compra', 3, 585.2, 0, 'USD', 968, 'xtb'),
  t('2026-01-20', 'SQM-B', 'compra', 40, 36200, 1810, 'CLP', 965, 'racional'),
  t('2026-02-03', 'AAPL', 'compra', 10, 228.5, 1, 'USD', 958, 'zesty'),
  t('2026-02-17', 'CHILE', 'compra', 5000, 138.4, 2076, 'CLP', 952, 'racional'),
  t('2026-03-09', 'NVDA', 'compra', 12, 148.9, 0, 'USD', 945, 'xtb'),
  t('2026-04-14', 'FALABELLA', 'compra', 300, 5120, 4608, 'CLP', 960, 'racional'),
  t('2026-05-22', 'AAPL', 'venta', 4, 246.8, 1, 'USD', 938, 'zesty', 'Toma parcial de ganancia'),
  t('2026-06-30', 'VOO', 'compra', 2, 601.75, 0, 'USD', 931, 'xtb'),
  t('2026-08-11', 'SQM-B', 'venta', 15, 39400, 1773, 'CLP', 949, 'racional'),
  t('2026-09-18', 'NVDA', 'compra', 6, 182.3, 0, 'USD', 955, 'xtb'),
  t('2026-10-27', 'FALABELLA', 'venta', 100, 4890, 1467, 'CLP', 947, 'racional', 'Reduzco exposición a retail'),
  t('2026-11-05', 'CHILE', 'compra', 2000, 149.8, 899, 'CLP', 944, 'racional'),
].map((x, i) => ({ ...x, id: `seed-${i + 1}`, fingerprint: fingerprint(x) }))

export const seedAlerts: AlertRule[] = [
  { id: 'a1', assetId: 'AAPL', kind: 'precio_sobre', level: 260, channels: ['push', 'telegram'], muted: false, createdAt: '2026-10-02T10:00:00' },
  { id: 'a2', assetId: 'SQM-B', kind: 'precio_bajo', level: 38000, channels: ['push'], muted: false, createdAt: '2026-10-10T10:00:00' },
  { id: 'a3', assetId: 'VOO', kind: 'cruce_medias', fast: 50, slow: 200, channels: ['push', 'correo'], muted: false, createdAt: '2026-10-15T10:00:00' },
  { id: 'a4', assetId: 'NVDA', kind: 'variacion_dia', pct: 4, channels: ['push'], muted: true, createdAt: '2026-10-20T10:00:00' },
  { id: 'a5', assetId: 'IPSA', kind: 'indicador', indicator: 'RSI', op: 'menor', threshold: 30, channels: ['push'], muted: false, createdAt: '2026-11-01T10:00:00' },
]

export const seedEvents: AlertEvent[] = [
  { id: 'e1', ruleId: 'a1', assetId: 'AAPL', triggeredAt: '2026-11-12T11:42:00', value: 261.15, message: 'Precio sobre US$260,00' },
  { id: 'e2', ruleId: 'a4', assetId: 'NVDA', triggeredAt: '2026-11-06T15:10:00', value: -4.6, message: 'Variación diaria de ±4% o más' },
]

export const seedSettings: Settings = {
  displayCurrency: 'CLP',
  lastBrokerId: 'racional',
  lastCurrency: 'CLP',
  autoRefreshMin: 5,
  theme: 'auto',
  autoLockMin: 1,
  notifyPush: true,
  notifyEmail: false,
  notifyTelegram: true,
}

/** Serie diaria determinista (ejemplo) que termina en el precio actual. */
export function sampleHistory(assetId: string, endPrice: number, n = 90): number[] {
  let seed = [...assetId].reduce((a, c) => a * 31 + c.charCodeAt(0), 7) >>> 0
  const rnd = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0
    return seed / 2 ** 32
  }
  const out = [1]
  for (let i = 1; i < n; i++) out.push(out[i - 1] * (1 + (rnd() - 0.47) * 0.03))
  const k = endPrice / out[n - 1]
  return out.map((v) => v * k)
}
