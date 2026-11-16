import { useEffect, useState, type FormEvent } from 'react'
import { Field, Seg, go } from '../components'
import { brokers } from '../data/seed'
import { parseNum } from '../lib/csv'
import { fmtMoney, today } from '../lib/format'
import { dollarForDates } from '../lib/quotes'
import { useApp } from '../store'
import type { Currency, Side } from '../types'

/** Formulario rápido: 4 campos visibles; el resto viene pre-llenado en "Más detalles". */
export function TradeForm({ assetId = '' }: { assetId?: string }) {
  const { state, assets, asset, addTrades, setSettings, fx } = useApp()
  const tradable = assets.filter((a) => a.type !== 'indice' && a.type !== 'divisa')
  const [aId, setAId] = useState(assetId)
  const [side, setSide] = useState<Side>('compra')
  const [qty, setQty] = useState('')
  const [price, setPrice] = useState('')
  const [more, setMore] = useState(false)
  const [date, setDate] = useState(today())
  const [fee, setFee] = useState('')
  const [tax, setTax] = useState('')
  const [currency, setCurrency] = useState<Currency>(asset(assetId)?.currency ?? state.settings.lastCurrency)
  const [brokerId, setBrokerId] = useState(state.settings.lastBrokerId)
  const [fxRate, setFxRate] = useState(String(fx).replace('.', ','))
  const [fxEdited, setFxEdited] = useState(false)
  const [note, setNote] = useState('')

  // El dólar de la operación es el de SU fecha. Se busca solo, salvo que el usuario lo haya escrito.
  useEffect(() => {
    if (fxEdited || state.demo) return
    let current = true
    if (date === today()) setFxRate(String(fx).replace('.', ','))
    else
      dollarForDates([date]).then(
        (r) => current && r[date] && setFxRate(String(r[date]).replace('.', ',')),
        () => {}, // sin conexión: queda el dólar actual, editable
      )
    return () => {
      current = false
    }
  }, [date, fxEdited, fx, state.demo])

  const q = parseNum(qty)
  const p = parseNum(price)
  const valid = !!aId && q !== null && q > 0 && p !== null && p > 0
  const total = valid ? q * p + (side === 'compra' ? 1 : -1) * ((parseNum(fee) ?? 0) + (parseNum(tax) ?? 0)) : 0

  const pickAsset = (id: string) => {
    setAId(id)
    const a = asset(id)
    if (a) setCurrency(a.currency)
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!valid) return
    addTrades([
      { date, assetId: aId, side, quantity: q, price: p, fee: parseNum(fee) ?? 0, tax: parseNum(tax) ?? 0, currency, fxRate: parseNum(fxRate) ?? fx, brokerId, note: note.trim() },
    ])
    setSettings({ lastBrokerId: brokerId, lastCurrency: currency })
    go('historial')
  }

  return (
    <form className="card form" onSubmit={submit}>
      <Seg label="Tipo de operación" value={side} onChange={setSide} options={[['compra', 'Compra'], ['venta', 'Venta']]} />
      <Field label="Activo">
        <select value={aId} onChange={(e) => pickAsset(e.target.value)} required>
          <option value="" disabled>Elegir…</option>
          {tradable.map((a) => <option key={a.id} value={a.id}>{a.id} — {a.name}</option>)}
        </select>
      </Field>
      <button type="button" className="link" onClick={() => go('activo')}>+ Agregar un activo que no está en la lista</button>
      <div className="grid2">
        <Field label="Cantidad">
          <input inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} placeholder="0" required />
        </Field>
        <Field label={`Precio (${currency})`}>
          <input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="0" required />
        </Field>
      </div>

      <button type="button" className="link" onClick={() => setMore(!more)} aria-expanded={more}>
        {more ? 'Ocultar detalles' : `Más detalles · ${brokers.find((b) => b.id === brokerId)?.name}, ${currency}, ${date === today() ? 'hoy' : date}`}
      </button>
      {more && (
        <>
          <div className="grid2">
            <Field label="Fecha"><input type="date" value={date} max={today()} onChange={(e) => setDate(e.target.value)} /></Field>
            <Field label="Broker">
              <select value={brokerId} onChange={(e) => setBrokerId(e.target.value)}>
                {brokers.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </Field>
          </div>
          <div className="grid2">
            <Field label="Comisión"><input inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} placeholder="0" /></Field>
            <Field label="Impuestos"><input inputMode="decimal" value={tax} onChange={(e) => setTax(e.target.value)} placeholder="0" /></Field>
          </div>
          <div className="grid2">
            <Field label="Moneda">
              <select value={currency} onChange={(e) => setCurrency(e.target.value as Currency)}>
                <option>CLP</option>
                <option>USD</option>
              </select>
            </Field>
            <Field label="Dólar de esa fecha (CLP)">
              <input inputMode="decimal" value={fxRate} onChange={(e) => (setFxRate(e.target.value), setFxEdited(true))} />
            </Field>
          </div>
          <Field label="Nota"><textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        </>
      )}

      <div className="between">
        <span className="muted">Total {side === 'compra' ? 'a pagar' : 'a recibir'}</span>
        <b className="num">{fmtMoney(total, currency)}</b>
      </div>
      <button className="btn primary" disabled={!valid}>Guardar operación</button>
      <p className="muted small">Solo queda registrada aquí. La orden la ejecutas tú en tu broker.</p>
    </form>
  )
}
