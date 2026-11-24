import { day, num } from '../format'
import type { Signal } from '../types'
import { SignalBadge } from './Badges'

function ScoreBar({ score, thr }: { score: number | null; thr: { buy: number; sell: number } }) {
  const pos = (v: number) => `${50 + v / 2}%`
  return (
    <div className="sbar-track" role="img" aria-label={score == null ? 'sin datos' : `puntaje ${num(score, 0)}`}>
      <span className="sbar-thr" style={{ left: pos(thr.sell) }} />
      <span className="sbar-thr" style={{ left: pos(thr.buy) }} />
      {score != null && score !== 0 && (
        <span className={`sbar-fill ${score > 0 ? 'pos' : 'neg'}`} style={{ width: `${Math.abs(score) / 2}%` }} />
      )}
    </div>
  )
}

export function SignalCard({ s, loading }: { s: Signal | null; loading: boolean }) {
  if (!s) return <section className="card"><p className="muted">{loading ? 'Calculando señal…' : 'Sin señal'}</p></section>
  const thr = s.thresholds ?? { buy: 30, sell: -30 }
  const strategyReasons = new Set(s.strategies.flatMap((v) => v.reasons))
  const extra = s.reasons.filter((r) => !strategyReasons.has(r))

  return (
    <section className="card" aria-labelledby="sig-title">
      <div className="card-head">
        <h2 id="sig-title">Señal</h2>
        <span className="muted small">
          vela del {day(s.date)}{s.provisional ? ' · provisional hasta el cierre' : ''}
        </span>
      </div>
      <div className="signal-grid">
        <div>
          <div className="signal-top">
            <SignalBadge label={s.label} big />
            {s.score != null && (
              <div>
                <div className="score-big num">{s.score > 0 ? '+' : ''}{num(s.score, 0)}</div>
                <div className="muted small">puntaje (−100 a +100)</div>
              </div>
            )}
            {s.confidence != null && (
              <div>
                <div className="small"><b className="num">{num(s.confidence, 0)} %</b> confianza</div>
                <div className="meter" aria-hidden="true"><span style={{ width: `${s.confidence}%` }} /></div>
              </div>
            )}
          </div>
          <p className="summary">{s.summary}</p>
          {s.actions && (
            <dl className="actions">
              <dt>Si la tienes</dt><dd>{s.actions.holding}</dd>
              <dt>Si no la tienes</dt><dd>{s.actions.not_holding}</dd>
            </dl>
          )}
          {s.previous && (
            <p className="small muted" style={{ margin: '4px 0' }}>
              {s.changed ? '↻ Cambió: ' : 'Anterior: '}
              {s.previous.label} ({s.previous.score > 0 ? '+' : ''}{num(s.previous.score, 0)}) el {day(s.previous.date)}
            </p>
          )}
          {(s.warnings ?? []).map((w) => <p key={w} className="warn" style={{ margin: '6px 0' }}>⚠ {w}</p>)}
          {extra.length > 0 && (
            <ul className="reasons">{extra.map((r) => <li key={r}>{r}</li>)}</ul>
          )}
        </div>
        <div>
          <h3 style={{ marginBottom: 8 }}>Por estrategia</h3>
          <div className="sbars">
            {s.strategies.map((v) => (
              <div key={v.key}>
                <div className="sbar-head">
                  <b>{v.label}{v.weight !== 1 ? <span className="muted small"> ×{num(v.weight, 1)}</span> : null}</b>
                  <span className="num">{v.score == null ? 'sin datos' : `${v.score > 0 ? '+' : ''}${num(v.score, 0)}`}</span>
                </div>
                <ScoreBar score={v.score} thr={thr} />
                <div className="sbar-reasons">{v.reasons.join(' · ')}</div>
              </div>
            ))}
          </div>
          <p className="muted small" style={{ margin: '10px 0 0' }}>
            Azul = a favor de comprar, rojo = a favor de vender. Las marcas son los umbrales {thr.sell} y +{thr.buy}.
          </p>
        </div>
      </div>
    </section>
  )
}
