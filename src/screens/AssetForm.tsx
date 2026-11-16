import { useState, type FormEvent } from 'react'
import { Field, go } from '../components'
import { parseNum } from '../lib/csv'
import { typeLabel } from '../lib/format'
import { fetchQuotes, quotesEnabled, yahooSymbol } from '../lib/quotes'
import { useApp } from '../store'
import type { Asset, AssetType, Currency } from '../types'

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
  // Símbolo del proveedor: se propone solo hasta que el usuario lo edita.
  const [customSymbol, setCustomSymbol] = useState<string | null>(null)
  const [lookup, setLookup] = useState<'' | 'buscando' | 'ok' | 'error'>('')

  const symbol = id.trim().toUpperCase()
  const provider = (customSymbol ?? (symbol ? yahooSymbol({ id: symbol, type, exchange }) : '')).trim().toUpperCase()
  const draft: Asset = { id: symbol, name: name.trim(), type, exchange, currency, providerSymbol: provider }
  const p = parseNum(price)
  const taken = !!asset(symbol)
  const valid = /^[A-Z0-9.\-]{1,12}$/.test(symbol) && !taken && !!draft.name && p !== null && p > 0

  const findPrice = async () => {
    setLookup('buscando')
    try {
      const { quotes } = await fetchQuotes([draft])
      if (!quotes.length) return setLookup('error')
      setPrice(String(quotes[0].price).replace('.', ','))
      setLookup('ok')
    } catch {
      setLookup('error')
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!valid) return
    addAsset(draft, p)
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

      <Field label="Símbolo en Yahoo Finance (para el precio automático)">
        <input value={provider} onChange={(e) => (setCustomSymbol(e.target.value), setLookup(''))} placeholder="Vacío = precio a mano" autoCapitalize="characters" autoComplete="off" />
      </Field>
      <p className="muted small">
        Se propone solo: en la Bolsa de Santiago es el símbolo más «.SN». Déjalo vacío si prefieres actualizar el precio a mano.
      </p>

      <Field label={`Precio actual (${currency})`}>
        <input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="0" required />
      </Field>
      {quotesEnabled() && provider && (
        <button type="button" className="btn" disabled={lookup === 'buscando' || !symbol} onClick={findPrice}>
          {lookup === 'buscando' ? 'Buscando…' : 'Buscar precio'}
        </button>
      )}
      {lookup === 'ok' && <p className="gain small">Precio encontrado para {provider}.</p>}
      {lookup === 'error' && <p className="loss small">No encontré precio en {currency} para «{provider}». Revisa el símbolo y la moneda, o ingresa el precio a mano.</p>}

      <button className="btn primary" disabled={!valid}>Guardar activo</button>
    </form>
  )
}
