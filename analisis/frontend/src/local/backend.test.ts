// Backend local de la app Android, sin red: IndexedDB simulado y un Yahoo/mindicador falsos con el formato real.
import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LocalBackend } from './backend'
import { Db } from './db'
import { localRequest } from './router'
import { setBases } from './net'
import { toCsv } from '../components/TransactionsCard'
import type { Transaction } from '../types'

const NOW = new Date('2026-11-23T22:00:00Z') // lunes, ambos mercados ya cerrados
const DAY = 864e5

interface Series { ts: number[]; o: number[]; h: number[]; l: number[]; c: number[]; v: (number | null)[] }

/** Serie diaria sintética terminada el 23/11/2026 (días hábiles), con el precio final indicado. */
function makeSeries(n: number, last: number, seed: number, openUtcMin: number): Series {
  let x = seed
  const rnd = () => ((x = (x * 16807) % 2147483647) / 2147483647)
  const days: number[] = []
  for (let d = Date.UTC(2026, 10, 23) / DAY; days.length < n; d--) {
    const wd = new Date(d * DAY).getUTCDay()
    if (wd !== 0 && wd !== 6) days.unshift(d)
  }
  const c = [last]
  for (let i = 1; i < n; i++) c.unshift(c[0] / (1 + (rnd() - 0.5) * 0.03))
  return {
    ts: days.map((d) => d * 86400 + openUtcMin * 60),
    o: c.map((v) => v * (1 + (rnd() - 0.5) * 0.004)),
    h: c.map((v) => v * 1.006), l: c.map((v) => v * 0.994), c, v: c.map(() => 1_000_000 + Math.floor(rnd() * 1e6)),
  }
}

const SERIES: Record<string, Series & { currency: string; tz: string; name: string }> = {
  SPY: { ...makeSeries(900, 722.29, 7, 870), currency: 'USD', tz: 'America/New_York', name: 'SPDR S&P 500' },
  VOO: { ...makeSeries(900, 663.91, 11, 870), currency: 'USD', tz: 'America/New_York', name: 'Vanguard S&P 500' },
  'SQM-B.SN': { ...makeSeries(900, 49780, 3, 750), currency: 'CLP', tz: 'America/Santiago', name: 'SQM' },
  'CHILE.SN': { ...makeSeries(900, 188.35, 5, 750), currency: 'CLP', tz: 'America/Santiago', name: 'Banco de Chile' },
  'CLP=X': { ...makeSeries(900, 1016, 9, 600), currency: 'CLP', tz: 'Europe/London', name: 'USD/CLP' },
}
// Velas de relleno de Yahoo en SQM-B (volumen 0, precio plano) durante 3 semanas.
{
  const s = SERIES['SQM-B.SN']
  for (let i = 700; i < 715; i++) {
    s.o[i] = s.h[i] = s.l[i] = s.c[i] = s.c[699]
    s.v[i] = 0
  }
}

let calls: string[] = []
let failYahoo = false
let yahoo429 = false
let mindicadorDown = false

function chartJson(sym: string, url: URL) {
  const s = SERIES[sym]
  const p1 = Number(url.searchParams.get('period1') ?? 0)
  const idx = s.ts.map((_, i) => i).filter((i) => s.ts[i] >= p1)
  const range = url.searchParams.get('range')
  const take = range === '5d' ? idx.slice(-5) : idx
  const n = s.c.length
  return {
    chart: {
      result: [{
        meta: { currency: s.currency, exchangeTimezoneName: s.tz, regularMarketPrice: s.c[n - 1],
          regularMarketTime: s.ts[n - 1] + 6 * 3600, longName: s.name, fullExchangeName: 'X' },
        timestamp: take.map((i) => s.ts[i]),
        indicators: {
          quote: [{ open: take.map((i) => s.o[i]), high: take.map((i) => s.h[i]), low: take.map((i) => s.l[i]),
            close: take.map((i) => s.c[i]), volume: take.map((i) => s.v[i]) }],
          adjclose: [{ adjclose: take.map((i) => s.c[i] * 0.98) }],
        },
      }],
      error: null,
    },
  }
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  calls = []
  failYahoo = false
  yahoo429 = false
  mindicadorDown = false
  setBases({ yahoo: 'https://y.test', mindicador: 'https://m.test' })
  vi.stubGlobal('fetch', async (input: string) => {
    const url = new URL(input)
    calls.push(url.pathname)
    if (url.host === 'm.test') {
      if (mindicadorDown) return json({}, 503)
      return json({ serie: [{ fecha: '2026-11-23T03:00:00.000Z', valor: 1015.3 }, { fecha: '2026-11-21T03:00:00.000Z', valor: 1021 }] })
    }
    const sym = decodeURIComponent(url.pathname.split('/').pop()!)
    if (failYahoo) return json({}, 500)
    if (yahoo429) return new Response('Too Many Requests', { status: 429 })
    if (!SERIES[sym]) return json({ chart: { result: null, error: { code: 'Not Found', description: 'No data found, symbol may be delisted' } } }, 404)
    return json(chartJson(sym, url))
  })
})

afterEach(() => vi.unstubAllGlobals())

let dbn = 0
async function make(clock = () => NOW) {
  const b = new LocalBackend(new Db(`test-${++dbn}`), clock)
  await b.init()
  await b.db.put('kv', ['SPY', 'SQM-B.SN'], 'watchlist')
  return b
}

describe('backend local', () => {
  it('ciclo completo: precios, historia, dólar, señales', async () => {
    const b = await make()
    const res = await b.refresh()
    expect(res.quotes).toEqual({ SPY: true, 'SQM-B.SN': true })
    expect(res.fx).toBe(true)
    expect(res.signals.sort()).toEqual(['SPY', 'SQM-B.SN'])
    const wl = await b.watchlist()
    expect(wl[0]).toMatchObject({ symbol: 'SPY', price: 722.29, currency: 'USD', status: 'ok' })
    expect(wl[0].freshness.kind).toBe('cierre')
    const fx = await b.fx()
    expect(fx.price).toBe(1015.3)
    expect(fx.freshness.kind).toBe('diario')
    const sig = await b.signal('SPY')
    expect(['COMPRAR', 'MANTENER', 'VENDER']).toContain(sig.label)
    expect(sig.strategies).toHaveLength(4)
    expect(sig.price.freshness.kind).toBe('cierre')
    const st = b.status()
    expect(st.disclaimer).toMatch(/educativa/)
    expect(st.markets.map((m) => m.market)).toEqual(['CL', 'US', 'FX'])
  })

  it('descarta las velas de relleno y avisa del hueco', async () => {
    const b = await make()
    await b.refresh()
    const bars = (await b.bars('SQM-B.SN'))!
    expect(bars.date.length).toBe(900 - 15)
    const sig = await b.signal('SQM-B.SN')
    expect(sig.warnings.some((w) => w.startsWith('Faltan datos reales'))).toBe(true)
  })

  it('dólar: si mindicador no responde se usa Yahoo', async () => {
    mindicadorDown = true
    const b = await make()
    await b.refresh()
    const fx = await b.fx()
    expect(fx.price).toBe(1016)
    expect(fx.source).toBe('yahoo (mercado)')
    expect(b.status().fx_sources_errors.mindicador).toBeTruthy()
  })

  it('si Yahoo limita las consultas (429), pausa en vez de insistir', async () => {
    let t = NOW.getTime()
    const b = await make(() => new Date(t))
    await b.refresh()
    yahoo429 = true
    const before = calls.length
    await b.refresh() // 1.ª llamada recibe 429 → pausa de 5 min; el resto ni se intenta
    expect(calls.length - before).toBeLessThanOrEqual(2) // SPY (429) + mindicador
    expect(b.status().providers[0].cooling_down_s).toBe(300)
    const q = await b.quoteView('SQM-B.SN')
    expect(q.error).toMatch(/se reintenta a las/)
    expect(q.price).toBe(49780) // se conserva el último precio
    yahoo429 = false
    t += 6 * 60e3
    await b.refresh()
    expect((await b.quoteView('SQM-B.SN')).status).toBe('ok')
  })

  it('agregar y quitar símbolos', async () => {
    const b = await make()
    await expect(b.addSymbol('NOEXISTE.SN')).rejects.toMatchObject({ status: 404 })
    await expect(b.addSymbol('mal!')).rejects.toMatchObject({ status: 422 })
    const v = await b.addSymbol(' voo ')
    expect(v.symbol).toBe('VOO')
    expect(await b.watchlistSymbols()).toContain('VOO')
    expect((await b.bars('VOO'))!.date.length).toBe(900)
    await b.removeSymbol('VOO')
    await expect(b.removeSymbol('VOO')).rejects.toMatchObject({ status: 404 })
  })

  it('fallas del proveedor: se guarda el error y se conserva el último precio', async () => {
    const b = await make()
    await b.refresh()
    failYahoo = true
    await b.refresh()
    const q = await b.quoteView('SPY')
    expect(q.price).toBe(722.29)
    expect(q.status).toBe('error')
  })

  it('cartera, aporte y alertas de caída con notificación', async () => {
    const b = await make()
    const notified: string[] = []
    b.onNewAlert = (a) => notified.push(a.title)
    await b.refresh()
    await expect(b.addTransaction({ symbol: 'SPY', side: 'venta', quantity: 1, price: 700, date: '2026-06-01' }))
      .rejects.toMatchObject({ status: 422 })
    await expect(b.addTransaction({ symbol: 'SPY', side: 'compra', quantity: 1, price: 700, date: '2999-01-01' }))
      .rejects.toMatchObject({ status: 422 })
    const t = await b.addTransaction({ symbol: 'SPY', side: 'compra', quantity: 10, price: 650, date: '2026-06-01', fee: 1 })
    expect(t.fx_source).toBe('historico')
    expect(t.currency).toBe('USD')
    // SQM comprado muy caro: -29 % → alerta de venta.
    await b.addTransaction({ symbol: 'SQM-B.SN', side: 'compra', quantity: 40, price: 70000, date: '2026-03-10' })
    const pf = await b.portfolio()
    expect(pf.positions.map((p) => p.symbol)).toEqual(['SPY', 'SQM-B.SN'])
    const sqm = pf.positions.find((p) => p.symbol === 'SQM-B.SN')!
    expect(sqm.status).toBe('vender')
    expect(pf.totals.value_usd).toBeCloseTo(pf.totals.value_clp / 1015.3, 6)

    const created = await b.evaluateAlerts()
    expect(created.map((a) => a.kind)).toContain('venta')
    expect(notified.some((x) => x.includes('VENTA'))).toBe(true)
    expect(await b.evaluateAlerts()).toEqual([]) // no se repite
    const al = await b.alerts()
    expect(al.unread).toBe(1)
    expect(al.by_severity.critical).toBe(1)
    await b.readAllAlerts()
    expect((await b.alerts()).unread).toBe(0)

    const c = await b.contribution(1_000_000)
    expect(c.excluded.find((e) => e.symbol === 'SQM-B.SN')?.reason).toMatch(/umbral de venta/)
    const sum = c.lines.reduce((a, l) => a + l.amount_clp, 0) + c.unallocated_clp
    expect(sum).toBeCloseTo(1_000_000, -1)

    const txs = await b.transactions()
    await expect(b.deleteTransaction(9999)).rejects.toMatchObject({ status: 404 })
    expect((await b.deleteTransaction(txs[0].id!)).deleted).toBe(txs[0].id)
  })

  it('importa el CSV de la PWA con vista previa y duplicados', async () => {
    const b = await make()
    await b.refresh()
    const csv = '﻿Fecha;Activo;Tipo;Cantidad;Precio;Comisión;Impuestos;Moneda;Tipo de cambio;Broker;Nota\r\n'
      + '2026-03-02;SPY;compra;2;650.5;1;0;USD;940.2;Racional;primera\r\n'
      + '2026-03-05;SQM-B;compra;10;48.500;0;0;CLP;945;Zesty;\r\n'
      + '05/04/2026;SPY;venta;1;700;1;0;USD;960;Racional;\r\n'
      + '2026-04-06;SPY;regalo;1;700;0;0;USD;960;Racional;\r\n'
    const prev = await b.importCsv(csv, false)
    expect([prev.ready, prev.errors, prev.imported]).toEqual([3, 1, 0])
    expect(prev.rows[1]).toMatchObject({ symbol: 'SQM-B.SN', price: 48500 })
    const done = await b.importCsv(csv, true)
    expect(done.imported).toBe(3)
    expect((await b.importCsv(csv, false)).duplicates).toBe(3)
  })

  it('respaldo: exportar e importar en otra app reproduce la cartera', async () => {
    const a = await make()
    await a.refresh()
    await a.addTransaction({ symbol: 'SPY', side: 'compra', quantity: 3, price: 650.5, date: '2026-06-01', fee: 1, note: 'uno; dos' })
    await a.addTransaction({ symbol: 'SQM-B.SN', side: 'compra', quantity: 40, price: 70000, date: '2026-03-10' })
    await a.addTransaction({ symbol: 'SPY', side: 'venta', quantity: 1, price: 700, date: '2026-09-01' })
    const csv = toCsv((await a.transactions()) as unknown as Transaction[])
    const b = await make()
    await b.refresh()
    const r = await b.importCsv(csv, true)
    expect(r.imported).toBe(3)
    const pa = await a.portfolio()
    const pb = await b.portfolio()
    expect(pb.totals.cost_clp).toBeCloseTo(pa.totals.cost_clp, 6)
    expect(pb.positions.map((p) => [p.symbol, p.quantity])).toEqual(pa.positions.map((p) => [p.symbol, p.quantity]))
    expect((await b.importCsv(csv, false)).duplicates).toBe(3)
  })

  it('backtest e historia con indicadores', async () => {
    const b = await make()
    await b.refresh()
    const h = await b.history('SPY', '3m')
    expect(h.bars.length).toBeGreaterThan(55)
    expect(h.bars[0].sma200).not.toBeNull()
    const ind = await b.indicators('SPY')
    expect(ind.history_days).toBe(900)
    const bt = await b.backtest({ symbol: 'SPY', years: 2, commission_pct: 0.1 })
    expect(bt.adjusted_prices).toBe(true)
    expect(bt.strategies.map((s) => s.key)).toEqual(['trend', 'rsi', 'macd', 'bollinger', 'composite'])
    const sh = await b.scoreHistory('SPY', '1m')
    expect(sh.points.length).toBeGreaterThan(15)
  })
})

describe('rutas locales (mismas que la API REST)', () => {
  it('GET y POST', async () => {
    const st = (await localRequest('/status')) as { disclaimer: string }
    expect(st.disclaimer).toMatch(/educativa/)
    await localRequest('/refresh', { method: 'POST' })
    const wl = (await localRequest('/watchlist')) as { symbol: string }[]
    expect(wl.length).toBeGreaterThan(0)
    await expect(localRequest('/nada')).rejects.toMatchObject({ status: 404 })
  })
})
