import { useState, type ChangeEvent, type FormEvent } from 'react'
import { api, LOCAL_MODE } from '../api'
import { day, num, parseNum, price, todayISO } from '../format'
import type { ImportResult, Transaction } from '../types'

const EMPTY = { symbol: '', side: 'compra' as 'compra' | 'venta', quantity: '', price: '', fee: '', fx: '', date: todayISO(), note: '' }
const IMPORT_STATUS: Record<string, string> = { lista: '✓ lista', duplicada: '↺ duplicada', error: '✕ error', importada: '✓ importada' }

/** Operaciones en el mismo CSV que exporta la PWA (se puede volver a importar aquí o en la otra app). */
export function toCsv(txs: Transaction[]): string {
  const esc = (v: string | number) => (/[";\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v))
  const rows = [['Fecha', 'Activo', 'Tipo', 'Cantidad', 'Precio', 'Comisión', 'Impuestos', 'Moneda', 'Tipo de cambio', 'Broker', 'Nota'],
    ...[...txs].reverse().map((t) => [t.date, t.symbol, t.side, t.quantity, t.price, t.fee, 0, t.currency, t.fx_rate, '', t.note ?? ''])]
  return rows.map((r) => r.map(esc).join(';')).join('\r\n')
}

function ExportBox({ txs }: { txs: Transaction[] }) {
  const [msg, setMsg] = useState<string | null>(null)
  const [shown, setShown] = useState<string | null>(null)
  const csv = () => toCsv(txs)
  async function copy() {
    try {
      await navigator.clipboard.writeText(csv())
      setMsg(`${txs.length} operaciones copiadas. Pégalas en una nota o correo como respaldo.`)
    } catch {
      setShown(csv()) // sin acceso al portapapeles: se muestra para copiar a mano
    }
  }
  function download() {
    const url = URL.createObjectURL(new Blob(['\uFEFF' + csv()], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `operaciones-${todayISO()}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }
  return (
    <div className="import">
      <h3 style={{ marginBottom: 6 }}>Respaldo</h3>
      <div className="chips">
        <button className="btn" onClick={copy} disabled={!txs.length}>Copiar operaciones (CSV)</button>
        {!LOCAL_MODE && <button className="btn" onClick={download} disabled={!txs.length}>Descargar CSV</button>}
      </div>
      <p className="muted small" style={{ margin: '6px 0 0' }}>
        Mismo formato que la PWA: se puede pegar abajo (en esta app o en la otra) para restaurar o pasar la cartera.
      </p>
      {msg && <p className="small">{msg}</p>}
      {shown && <textarea className="input paste" readOnly value={shown} onFocus={(e) => e.target.select()} />}
    </div>
  )
}

function ImportBox({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [prev, setPrev] = useState<ImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [pasted, setPasted] = useState('')

  async function review(t: string, label: string) {
    setName(label)
    setError(null)
    setPrev(null)
    setText(t)
    try {
      setPrev(await api.importCsv(t, false))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function pick(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (f) await review(await f.text(), f.name)
  }

  async function commit() {
    if (!text) return
    setBusy(true)
    setError(null)
    try {
      const r = await api.importCsv(text, true)
      setPrev(r)
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="import">
      <h3 style={{ marginBottom: 6 }}>Importar desde la PWA</h3>
      <p className="muted small" style={{ margin: '0 0 6px' }}>
        En la PWA: Historial → Exportar CSV. También sirve otro CSV con columnas Fecha, Activo, Tipo, Cantidad, Precio.
      </p>
      <input type="file" accept=".csv,.txt,text/csv" onChange={pick} aria-label="Archivo CSV" />
      <textarea className="input paste" placeholder="…o pega aquí el contenido del CSV" value={pasted}
                onChange={(e) => setPasted(e.target.value)} aria-label="Contenido del CSV" />
      <button className="btn" style={{ marginTop: 6 }} disabled={!pasted.trim()} onClick={() => review(pasted, 'Texto pegado')}>
        Revisar texto pegado
      </button>
      {error && <p className="error" role="alert">{error}</p>}
      {prev && (
        <>
          <p className="small" style={{ margin: '8px 0 4px' }}>
            {name}: <b>{prev.ready}</b> listas · {prev.duplicates} duplicadas · {prev.errors} con error
            {prev.imported > 0 && <> · <b>{prev.imported} importadas</b></>}
          </p>
          {prev.consistency_error && <p className="warn">⚠ {prev.consistency_error}</p>}
          <div className="table-wrap" style={{ maxHeight: 260, overflowY: 'auto' }}>
            <table className="bt">
              <thead><tr><th>Línea</th><th>Estado</th><th>Fecha</th><th>Símbolo</th><th>Tipo</th><th>Cantidad</th><th>Precio</th></tr></thead>
              <tbody>
                {prev.rows.map((r) => (
                  <tr key={r.line} title={r.error ?? ''}>
                    <td>{r.line}</td>
                    <td className={r.status === 'error' ? 'loss' : r.status === 'duplicada' ? 'muted' : ''}>
                      {IMPORT_STATUS[r.status]}{r.error ? `: ${r.error}` : ''}
                    </td>
                    <td>{r.date ? day(r.date) : '—'}</td>
                    <td>{r.symbol ?? r.raw[1] ?? '—'}</td>
                    <td>{r.side ?? '—'}</td>
                    <td className="num">{r.quantity != null ? num(r.quantity, r.quantity % 1 ? 4 : 0) : '—'}</td>
                    <td className="num">{r.price != null ? num(r.price, 2) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {prev.imported === 0 && (
            <button className="btn primary" style={{ marginTop: 8 }} onClick={commit}
                    disabled={busy || prev.ready === 0 || !!prev.consistency_error}>
              {busy ? 'Importando…' : `Importar ${prev.ready} operaciones`}
            </button>
          )}
        </>
      )}
    </div>
  )
}

export function TransactionsCard({ txs, onChanged }: { txs: Transaction[] | null; onChanged: () => void }) {
  const [f, setF] = useState(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof EMPTY) => (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: e.target.value })

  async function submit(e: FormEvent) {
    e.preventDefault()
    const quantity = parseNum(f.quantity)
    const p = parseNum(f.price)
    if (!f.symbol.trim() || !quantity || !p) {
      setError('Completa símbolo, cantidad y precio')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.addTransaction({
        symbol: f.symbol.trim(), side: f.side, quantity, price: p, date: f.date,
        fee: parseNum(f.fee) ?? 0, fx_rate: parseNum(f.fx), note: f.note.trim() || null,
      })
      setF({ ...EMPTY, date: f.date, side: f.side })
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function remove(t: Transaction) {
    if (!window.confirm(`¿Borrar la ${t.side} de ${num(t.quantity, t.quantity % 1 ? 4 : 0)} ${t.symbol} del ${day(t.date)}?`)) return
    try {
      await api.deleteTransaction(t.id)
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <section className="card" aria-labelledby="tx-title">
      <div className="card-head">
        <h2 id="tx-title">Operaciones</h2>
        <span className="muted small">{txs ? `${txs.length} registradas` : ''}</span>
      </div>
      <form className="tx-form" onSubmit={submit}>
        <label>Fecha<input className="input" type="date" value={f.date} max={todayISO()} onChange={set('date')} required /></label>
        <label>Tipo
          <select className="input" value={f.side} onChange={set('side')}>
            <option value="compra">Compra</option>
            <option value="venta">Venta</option>
          </select>
        </label>
        <label>Símbolo<input className="input" value={f.symbol} onChange={set('symbol')} placeholder="SQM-B.SN" spellCheck={false} /></label>
        <label>Cantidad<input className="input" inputMode="decimal" value={f.quantity} onChange={set('quantity')} placeholder="10" /></label>
        <label>Precio por acción<input className="input" inputMode="decimal" value={f.price} onChange={set('price')} placeholder="48.500" /></label>
        <label>Comisión<input className="input" inputMode="decimal" value={f.fee} onChange={set('fee')} placeholder="0" /></label>
        <label title="CLP por 1 USD. Si lo dejas vacío se usa el de esa fecha.">Dólar (opcional)
          <input className="input" inputMode="decimal" value={f.fx} onChange={set('fx')} placeholder="auto" />
        </label>
        <label className="grow">Nota<input className="input" value={f.note} onChange={set('note')} maxLength={500} /></label>
        <button className="btn primary" disabled={busy}>{busy ? 'Guardando…' : 'Registrar'}</button>
      </form>
      <p className="muted small" style={{ margin: '6px 0 0' }}>
        Precio y comisión en la moneda del activo (CLP para .SN, USD para EE.UU.). No se puede vender más de lo que tienes.
      </p>
      {error && <p className="error" role="alert">{error}</p>}

      {txs && txs.length > 0 && (
        <div className="table-wrap" style={{ maxHeight: 340, overflowY: 'auto' }}>
          <table className="bt">
            <thead>
              <tr><th>Fecha</th><th>Tipo</th><th>Símbolo</th><th>Cantidad</th><th>Precio</th><th>Comisión</th><th>Dólar</th><th>Nota</th><th /></tr>
            </thead>
            <tbody>
              {txs.map((t) => (
                <tr key={t.id}>
                  <td>{day(t.date)}</td>
                  <td className={t.side === 'compra' ? '' : 'muted'}>{t.side}</td>
                  <td><b>{t.symbol}</b></td>
                  <td className="num">{num(t.quantity, t.quantity % 1 ? 4 : 0)}</td>
                  <td className="num">{price(t.price, t.currency)} <span className="muted small">{t.currency}</span></td>
                  <td className="num">{price(t.fee, t.currency)}</td>
                  <td className="num" title={t.fx_source === 'manual' ? 'ingresado a mano' : t.fx_source === 'historico' ? 'de esa fecha' : 'aproximado con el de hoy'}>
                    {num(t.fx_rate, 0)}{t.fx_source === 'actual' ? '*' : ''}
                  </td>
                  <td className="small" style={{ textAlign: 'left' }}>{t.note ?? ''}</td>
                  <td><button className="btn icon" onClick={() => remove(t)} aria-label={`Borrar operación ${t.id}`} title="Borrar">×</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {txs && txs.length > 0 && <ExportBox txs={txs} />}
      <ImportBox onDone={onChanged} />
    </section>
  )
}
