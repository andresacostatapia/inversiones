import { useState, type FormEvent } from 'react'
import { api } from '../api'
import { clp, num, parseNum, pct, price } from '../format'
import type { ContributionResult } from '../types'
import { SignalBadge } from './Badges'

export function ContributionCard({ defaultAmount }: { defaultAmount: number }) {
  const [amount, setAmount] = useState(defaultAmount ? num(defaultAmount, 0) : '')
  const [res, setRes] = useState<ContributionResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function run(e: FormEvent) {
    e.preventDefault()
    const v = parseNum(amount)
    if (!v || v <= 0) {
      setError('Ingresa un monto en CLP mayor que 0')
      return
    }
    setBusy(true)
    setError(null)
    try {
      setRes(await api.contribution(v))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card" aria-labelledby="ct-title">
      <div className="card-head">
        <h2 id="ct-title">Reparto del aporte mensual</h2>
      </div>
      <form className="bt-form" onSubmit={run}>
        <label>
          Aporte en CLP
          <input className="input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)}
                 style={{ width: 160 }} placeholder="500.000" />
        </label>
        <button className="btn primary" disabled={busy}>{busy ? 'Calculando…' : 'Calcular reparto'}</button>
      </form>
      <p className="muted small" style={{ margin: '8px 0 0' }}>
        Se reparte para acercarte a tu distribución objetivo. Dentro de cada mercado va a lo que tienes o vigilas,
        sin señal VENDER (COMPRAR pesa el doble que MANTENER). Nunca sugiere vender.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {res && (
        <>
          <div className="table-wrap">
            <table className="bt">
              <thead>
                <tr><th>Mercado</th><th>Hoy</th><th>Objetivo</th><th>Aporte</th><th>Después</th></tr>
              </thead>
              <tbody>
                {res.buckets.map((b) => (
                  <tr key={b.bucket}>
                    <td>{b.name}</td>
                    <td className="num">{pct(b.current_pct, 1, false)}</td>
                    <td className="num">{pct(b.target_pct, 0, false)}</td>
                    <td className="num">{clp(b.amount_clp)}</td>
                    <td className="num"><b>{pct(b.after_pct, 1, false)}</b></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3 style={{ margin: '14px 0 4px' }}>Compras sugeridas</h3>
          {res.lines.length === 0 ? (
            <p className="muted">No hay compras sugeridas con este monto.</p>
          ) : (
            <div className="table-wrap">
              <table className="bt">
                <thead>
                  <tr><th>Símbolo</th><th>Señal</th><th>Cantidad</th><th>Precio</th><th>Monto</th><th>Motivo</th></tr>
                </thead>
                <tbody>
                  {res.lines.map((l) => (
                    <tr key={l.symbol}>
                      <td><b>{l.symbol}</b><div className="small muted ellipsis">{l.name ?? ''}</div></td>
                      <td><SignalBadge label={l.signal} /></td>
                      <td className="num">{num(l.quantity, l.quantity % 1 ? 4 : 0)}</td>
                      <td className="num">{price(l.price_native, l.currency)} <span className="muted small">{l.currency}</span></td>
                      <td className="num">{clp(l.amount_clp)}</td>
                      <td className="small" style={{ textAlign: 'left', whiteSpace: 'normal' }}>{l.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="small" style={{ margin: '8px 0 0' }}>
            Sin invertir: <b className="num">{clp(res.unallocated_clp)}</b> · dólar usado {clp(res.fx)}
          </p>
          {res.excluded.length > 0 && (
            <p className="small muted" style={{ margin: '4px 0 0' }}>
              Excluidos: {res.excluded.map((e) => `${e.symbol} (${e.reason})`).join(' · ')}
            </p>
          )}
          <ul className="verdicts">{res.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </>
      )}
    </section>
  )
}
