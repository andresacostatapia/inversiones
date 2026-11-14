import { useState, type FormEvent } from 'react'
import { Field, go } from '../components'
import { parseNum } from '../lib/csv'
import { typeLabel } from '../lib/format'
import { useApp } from '../store'
import type { AssetType, Currency } from '../types'

const EXCHANGES: [string, Currency][] = [
  ['Bolsa de Santiago', 'CLP'],
  ['NASDAQ', 'USD'],
  ['NYSE', 'USD'],
  ['Otro', 'USD'],
]

export function AssetForm() {
  const { asset, addAsset } = useApp()
  const [id, setId] = useState('')
  const [name, setName] = useState('')
  const [type, setType] = useState<AssetType>('accion')
  const [exchange, setExchange] = useState('Bolsa de Santiago')
  const [currency, setCurrency] = useState<Currency>('CLP')
  const [price, setPrice] = useState('')

  const symbol = id.trim().toUpperCase()
  const p = parseNum(price)
  const taken = !!asset(symbol)
  const valid = /^[A-Z0-9.\-]{1,12}$/.test(symbol) && !taken && !!name.trim() && p !== null && p > 0

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!valid) return
    addAsset({ id: symbol, name: name.trim(), type, exchange, currency }, p)
    go(type === 'indice' || type === 'divisa' ? 'portafolio' : `nueva/${symbol}`)
  }

  return (
    <form className="card form" onSubmit={submit}>
      <div className="grid2">
        <Field label="Símbolo">
          <input value={id} onChange={(e) => setId(e.target.value)} placeholder="COPEC" autoCapitalize="characters" autoComplete="off" required />
        </Field>
        <Field label="Tipo">
          <select value={type} onChange={(e) => setType(e.target.value as AssetType)}>
            {(Object.keys(typeLabel) as AssetType[]).map((t) => <option key={t} value={t}>{typeLabel[t]}</option>)}
          </select>
        </Field>
      </div>
      {taken && <p className="loss small">Ya tienes un activo con ese símbolo.</p>}
      <Field label="Nombre">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Empresas Copec" required />
      </Field>
      <div className="grid2">
        <Field label="Mercado">
          <select
            value={exchange}
            onChange={(e) => {
              setExchange(e.target.value)
              setCurrency(EXCHANGES.find(([x]) => x === e.target.value)?.[1] ?? 'USD')
            }}
          >
            {EXCHANGES.map(([x]) => <option key={x}>{x}</option>)}
          </select>
        </Field>
        <Field label="Moneda en que cotiza">
          <select value={currency} onChange={(e) => setCurrency(e.target.value as Currency)}>
            <option>CLP</option>
            <option>USD</option>
          </select>
        </Field>
      </div>
      <Field label={`Precio actual (${currency})`}>
        <input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="0" required />
      </Field>
      <button className="btn primary" disabled={!valid}>Guardar activo</button>
      <p className="muted small">Mientras no haya cotizaciones automáticas, el precio se actualiza a mano en el detalle del activo.</p>
    </form>
  )
}
