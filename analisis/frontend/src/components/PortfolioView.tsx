import { clp, money, num, pct, price, signClass } from '../format'
import type { Allocation, Portfolio, RiskStatus } from '../types'
import { FreshnessBadge } from './Badges'

const STATUS: Record<RiskStatus, { icon: string; label: string; cls: string }> = {
  ok: { icon: '✓', label: 'OK', cls: 'st-ok' },
  revisar: { icon: '⚠', label: 'Revisar', cls: 'st-review' },
  vender: { icon: '⛔', label: 'Vender', cls: 'st-sell' },
  sin_precio: { icon: '?', label: 'Sin precio', cls: 'st-none' },
}

export function StatusChip({ s }: { s: RiskStatus }) {
  const v = STATUS[s]
  return <span className={`status ${v.cls}`}><span aria-hidden="true">{v.icon}</span>{v.label}</span>
}

function Tile({ k, v, cls, sub }: { k: string; v: string; cls?: string; sub?: string }) {
  return (
    <div className="tile">
      <div className="k">{k}</div>
      <div className={`v num ${cls ?? ''}`}>{v}</div>
      {sub && <div className="small muted num">{sub}</div>}
    </div>
  )
}

function AllocationBars({ alloc }: { alloc: Allocation[] }) {
  const color = { CL: 'var(--series-1)', INTL: 'var(--series-2)' }
  return (
    <div className="alloc">
      {alloc.map((a) => (
        <div key={a.bucket}>
          <div className="sbar-head">
            <b>{a.name}</b>
            <span className="num">
              {pct(a.pct, 1, false)} <span className="muted">· objetivo {pct(a.target_pct, 0, false)}</span>{' '}
              <span className={Math.abs(a.diff_pct) >= 5 ? 'warn-text' : 'muted'}>({pct(a.diff_pct)})</span>
            </span>
          </div>
          <div className="alloc-track" role="img" aria-label={`${a.name}: ${num(a.pct, 1)} % actual, objetivo ${num(a.target_pct, 0)} %`}>
            <span className="alloc-fill" style={{ width: `${Math.min(100, a.pct)}%`, background: color[a.bucket] }} />
            <span className="alloc-target" style={{ left: `${a.target_pct}%` }} title={`Objetivo ${a.target_pct} %`} />
          </div>
        </div>
      ))}
      <p className="muted small" style={{ margin: 0 }}>La marca vertical es el objetivo de tu perfil.</p>
    </div>
  )
}

export function PortfolioView({ data, onSelect }: { data: Portfolio; onSelect: (s: string) => void }) {
  const t = data.totals
  const th = data.thresholds
  const hasPositions = data.positions.length > 0

  return (
    <>
      <section className="card" aria-labelledby="pf-title">
        <div className="card-head">
          <h2 id="pf-title">Resumen de la cartera</h2>
          <span className="muted small">
            Dólar {money(data.fx_now, 'CLP')} · <FreshnessBadge f={data.fx?.freshness} />
          </span>
        </div>
        {!hasPositions ? (
          <p className="muted">Todavía no hay posiciones. Registra una compra abajo o importa el CSV de tu PWA.</p>
        ) : (
          <div className="signal-grid">
            <div className="tiles">
              <Tile k="Valor total" v={clp(t.value_clp)} sub={t.value_usd != null ? `US$ ${num(t.value_usd, 0)}` : undefined} />
              <Tile k="Ganancia no realizada" v={clp(t.unrealized_clp, true)} cls={signClass(t.unrealized_clp)}
                    sub={pct(t.unrealized_pct)} />
              <Tile k="Ganancia realizada" v={clp(t.realized_clp, true)} cls={signClass(t.realized_clp)} />
              <Tile k="Variación de hoy" v={clp(t.day_change_clp, true)} cls={signClass(t.day_change_clp)} />
              <Tile k="Invertido (costo)" v={clp(t.cost_clp)} />
            </div>
            <AllocationBars alloc={data.allocation} />
          </div>
        )}
        {data.missing_prices.length > 0 && (
          <p className="warn">⚠ Sin precio para {data.missing_prices.join(', ')}: no se suman al total.</p>
        )}
      </section>

      {hasPositions && (
        <section className="card" aria-labelledby="pos-title">
          <div className="card-head">
            <h2 id="pos-title">Posiciones</h2>
            <span className="muted small">
              Alertas: revisar ≤ {pct(th.review_pct, 0)} · vender ≤ {pct(th.sell_pct, 0)} desde tu costo promedio
            </span>
          </div>
          <div className="table-wrap">
            <table className="bt pos">
              <thead>
                <tr>
                  <th>Símbolo</th><th>Cantidad</th><th>Costo prom.</th><th>Precio</th><th>Valor CLP</th>
                  <th>Ganancia CLP</th><th title="Precio actual vs. costo promedio, en la moneda del activo">Vs. compra</th>
                  <th>Estado</th><th>Peso</th>
                </tr>
              </thead>
              <tbody>
                {data.positions.map((p) => (
                  <tr key={p.symbol}>
                    <td>
                      <button className="linkish" onClick={() => onSelect(p.symbol)} title="Ver en el mercado">{p.symbol}</button>
                      <div className="small muted ellipsis">{p.name ?? ''}</div>
                    </td>
                    <td className="num">{num(p.quantity, p.quantity % 1 ? 4 : 0)}</td>
                    <td className="num">{price(p.avg_cost, p.currency)} <span className="muted small">{p.currency}</span></td>
                    <td className="num">
                      {price(p.price, p.currency)}
                      <div><FreshnessBadge f={p.freshness ?? undefined} short /></div>
                    </td>
                    <td className="num">{clp(p.value_clp)}</td>
                    <td className={`num ${signClass(p.unrealized_clp)}`}>
                      {clp(p.unrealized_clp, true)}
                      <div className="small">{pct(p.unrealized_pct)}</div>
                      {p.currency === 'USD' && p.fx_effect_clp != null && (
                        <div className="small muted" title="Separación de la ganancia en CLP">
                          precio {clp(p.price_effect_clp, true)} · dólar {clp(p.fx_effect_clp, true)}
                        </div>
                      )}
                    </td>
                    <td className={`num ${signClass(p.drawdown_pct)}`}>{pct(p.drawdown_pct)}</td>
                    <td><StatusChip s={p.status} /></td>
                    <td className="num">{pct(p.weight_pct, 1, false)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.closed.length > 0 && (
            <p className="small muted" style={{ margin: '8px 0 0' }}>
              Posiciones cerradas: {data.closed.map((c) => `${c.symbol} ${clp(c.realized_clp, true)}`).join(' · ')}
            </p>
          )}
        </section>
      )}
    </>
  )
}
