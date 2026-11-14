import { useState } from 'react'
import { Field, go } from '../components'
import { brokers } from '../data/seed'
import { fingerprint, hashText, parseCsv, parseDate, parseNum } from '../lib/csv'
import { fmtDate, fmtPrice, fmtQty } from '../lib/format'
import { uid, useApp } from '../store'
import type { Currency, NewTrade, Side } from '../types'

// Archivo de ejemplo: 2 filas nuevas, 1 ya registrada, 1 con ticker desconocido y 1 con cantidad ilegible.
const SAMPLE = `Fecha;Ticker;Operación;Cantidad;Precio;Comisión;Moneda;Nota
12/11/2026;AAPL;Compra;3;262,10;1,00;USD;Compra mensual
11/11/2026;CHILE;Compra;1500;152,1;685;CLP;
05/11/2026;CHILE;Compra;2000;149,8;899;CLP;Ya registrada antes
10/11/2026;COPEC;Compra;20;6.850;411;CLP;Ticker que la app no conoce
09/11/2026;NVDA;Venta;dos;175,40;0;USD;Cantidad ilegible
`

interface Draft {
  key: number
  date: string
  rawSymbol: string
  assetId: string
  side: Side | ''
  quantity: number | null
  price: number | null
  fee: number
  currency: Currency | ''
  note: string
}

const norm = (s: string) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').trim()
const COLS: Record<string, string[]> = {
  date: ['fecha', 'date', 'time', 'open time'],
  symbol: ['ticker', 'activo', 'symbol', 'simbolo', 'instrumento', 'nemotecnico'],
  side: ['operacion', 'tipo', 'side', 'type'],
  qty: ['cantidad', 'qty', 'quantity', 'volume', 'volumen', 'unidades'],
  price: ['precio', 'price', 'open price'],
  fee: ['comision', 'fee', 'commission'],
  currency: ['moneda', 'currency'],
  note: ['nota', 'note', 'comment', 'comentario'],
}

type Status = { kind: 'ok' | 'duplicado' | 'error'; msg: string }

export function Import() {
  const { state, assets, asset, addTrades, setSettings, fx, brokerName } = useApp()
  const [brokerId, setBrokerId] = useState(state.settings.lastBrokerId)
  const [file, setFile] = useState<{ name: string; hash: string } | null>(null)
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [editing, setEditing] = useState(-1)
  const [error, setError] = useState('')
  const [done, setDone] = useState(-1)
  const tradable = assets.filter((a) => a.type !== 'indice' && a.type !== 'divisa')

  const loadText = (name: string, text: string) => {
    const rows = parseCsv(text)
    const header = (rows[0] ?? []).map(norm)
    const col = (k: string) => header.findIndex((h) => COLS[k].includes(h))
    const idx = Object.fromEntries(Object.keys(COLS).map((k) => [k, col(k)]))
    if (idx.date < 0 || idx.symbol < 0 || idx.qty < 0 || idx.price < 0) {
      setError('No encontré las columnas de fecha, activo, cantidad y precio en la primera fila.')
      return
    }
    const cell = (r: string[], k: string) => (idx[k] >= 0 ? (r[idx[k]] ?? '') : '')
    setError('')
    setFile({ name, hash: hashText(text) })
    setDrafts(
      rows.slice(1).map((r, i) => {
        const rawSymbol = cell(r, 'symbol').trim().toUpperCase()
        const a = asset(rawSymbol)
        const sideTxt = norm(cell(r, 'side'))
        const curTxt = cell(r, 'currency').trim().toUpperCase()
        return {
          key: i,
          date: parseDate(cell(r, 'date')),
          rawSymbol,
          assetId: a?.id ?? '',
          side: /compra|buy/.test(sideTxt) ? 'compra' : /venta|sell/.test(sideTxt) ? 'venta' : '',
          quantity: parseNum(cell(r, 'qty')),
          price: parseNum(cell(r, 'price')),
          fee: parseNum(cell(r, 'fee')) ?? 0,
          currency: curTxt === 'CLP' || curTxt === 'USD' ? curTxt : (a?.currency ?? ''),
          note: cell(r, 'note').trim(),
        }
      }),
    )
  }

  const toTrade = (d: Draft): NewTrade | null =>
    d.date && d.assetId && d.side && d.quantity && d.price
      ? { date: d.date, assetId: d.assetId, side: d.side, quantity: d.quantity, price: d.price, fee: d.fee, tax: 0, currency: d.currency || asset(d.assetId)!.currency, fxRate: fx, brokerId, note: d.note }
      : null

  // Estado de cada fila: se recalcula al corregir, así una fila arreglada pasa a "ok".
  const existing = new Set(state.trades.map((t) => t.fingerprint))
  const seen = new Set<string>()
  const status: Status[] = drafts.map((d) => {
    if (!d.date) return { kind: 'error', msg: 'Fecha no reconocida' }
    if (!d.assetId) return { kind: 'error', msg: `Activo «${d.rawSymbol || '?'}» no reconocido` }
    if (!d.side) return { kind: 'error', msg: 'Tipo de operación no reconocido' }
    if (!d.quantity || d.quantity <= 0) return { kind: 'error', msg: 'Cantidad no reconocida' }
    if (!d.price || d.price <= 0) return { kind: 'error', msg: 'Precio no reconocido' }
    const fp = fingerprint(toTrade(d)!)
    if (existing.has(fp)) return { kind: 'duplicado', msg: 'Ya está en tu historial' }
    if (seen.has(fp)) return { kind: 'duplicado', msg: 'Repetida dentro del archivo' }
    seen.add(fp)
    return { kind: 'ok', msg: 'Lista para guardar' }
  })
  const count = (k: Status['kind']) => status.filter((s) => s.kind === k).length
  const patch = (key: number, p: Partial<Draft>) => setDrafts((ds) => ds.map((d) => (d.key === key ? { ...d, ...p } : d)))
  const previous = file && state.batches.find((b) => b.fileHash === file.hash)

  const confirmImport = () => {
    const batchId = uid()
    const list = drafts.filter((_, i) => status[i].kind === 'ok').map((d) => ({ ...toTrade(d)!, importBatchId: batchId }))
    addTrades(list, { id: batchId, brokerId, fileName: file!.name, fileHash: file!.hash, importedAt: new Date().toISOString(), rowCount: list.length })
    setSettings({ lastBrokerId: brokerId })
    setDone(list.length)
  }

  if (done >= 0)
    return (
      <section className="card form">
        <b>Se guardaron {done} operaciones de {brokerName(brokerId)}.</b>
        <p className="muted">Las filas duplicadas o con errores no se guardaron.</p>
        <button className="btn primary" onClick={() => go('historial')}>Ver historial</button>
      </section>
    )

  if (!file)
    return (
      <section className="card form">
        <Field label="Broker del archivo">
          <select value={brokerId} onChange={(e) => setBrokerId(e.target.value)}>
            {brokers.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </Field>
        <Field label="Archivo CSV o estado de cuenta">
          <input
            type="file"
            accept=".csv,.txt,text/csv"
            onChange={async (e) => {
              const f = e.target.files?.[0]
              if (f) loadText(f.name, await f.text())
            }}
          />
        </Field>
        {error && <p className="loss">{error}</p>}
        <button className="btn" onClick={() => loadText('ejemplo-racional.csv', SAMPLE)}>Probar con un archivo de ejemplo</button>
        <p className="muted small">
          El archivo se lee en tu celular y no se envía a ningún servidor. Nada se guarda hasta que confirmes.
        </p>
      </section>
    )

  return (
    <>
      <section className="card">
        <b>{file.name}</b> · {brokerName(brokerId)}
        <div className="muted num mt">
          {drafts.length} filas: {count('ok')} listas · {count('duplicado')} duplicadas · {count('error')} por corregir
        </div>
        {previous && <p className="warn">Este mismo archivo ya se importó el {fmtDate(previous.importedAt, true)}. Las filas repetidas se omiten.</p>}
      </section>

      <section className="card list">
        {drafts.map((d, i) => {
          const s = status[i]
          const a = asset(d.assetId)
          return (
            <div key={d.key}>
              <button className="row" onClick={() => setEditing(editing === d.key ? -1 : d.key)} aria-expanded={editing === d.key}>
                <div>
                  <b>{d.assetId || d.rawSymbol || '—'}</b> {d.side && <span className={`chip ${d.side}`}>{d.side}</span>}
                  <div className="muted num">
                    {d.date ? fmtDate(d.date) : 'sin fecha'} · {d.quantity ? fmtQty(d.quantity) : '?'} × {d.price ? fmtPrice(d.price, d.currency || a?.currency || 'CLP') : '?'}
                  </div>
                </div>
                <div className="right">
                  <span className={`badge ${s.kind}`}>{s.kind === 'ok' ? '✓ Lista' : s.kind === 'duplicado' ? '= Duplicada' : '! Corregir'}</span>
                  <div className="muted small">{s.msg}</div>
                </div>
              </button>
              {editing === d.key && (
                <div className="detail form">
                  <div className="grid2">
                    <Field label="Fecha"><input type="date" value={d.date} onChange={(e) => patch(d.key, { date: e.target.value })} /></Field>
                    <Field label="Activo">
                      <select value={d.assetId} onChange={(e) => patch(d.key, { assetId: e.target.value, currency: asset(e.target.value)?.currency ?? '' })}>
                        <option value="">Elegir…</option>
                        {tradable.map((x) => <option key={x.id}>{x.id}</option>)}
                      </select>
                    </Field>
                  </div>
                  <div className="grid3">
                    <Field label="Tipo">
                      <select value={d.side} onChange={(e) => patch(d.key, { side: e.target.value as Side })}>
                        <option value="">—</option>
                        <option value="compra">Compra</option>
                        <option value="venta">Venta</option>
                      </select>
                    </Field>
                    <Field label="Cantidad"><input inputMode="decimal" defaultValue={d.quantity ?? ''} onChange={(e) => patch(d.key, { quantity: parseNum(e.target.value) })} /></Field>
                    <Field label="Precio"><input inputMode="decimal" defaultValue={d.price ?? ''} onChange={(e) => patch(d.key, { price: parseNum(e.target.value) })} /></Field>
                  </div>
                  <button className="link danger" onClick={() => setDrafts((ds) => ds.filter((x) => x.key !== d.key))}>Descartar fila</button>
                </div>
              )}
            </div>
          )
        })}
      </section>

      <div className="grid2">
        <button className="btn" onClick={() => (setFile(null), setDrafts([]))}>Cancelar</button>
        <button className="btn primary" disabled={count('ok') === 0} onClick={confirmImport}>Guardar {count('ok')} operaciones</button>
      </div>
      <p className="foot muted">Toca una fila para corregirla. Solo se guardan las marcadas como «Lista».</p>
    </>
  )
}
