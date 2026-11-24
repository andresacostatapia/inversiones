import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api } from '../api'
import type { CfdAllTf, CfdBias, CfdContract, CfdDetail, CfdOverview, CfdRow, CfdRule, CfdSettings, CfdTf, CfdTicket, CfdVerdict } from '../cfdTypes'
import { dateTime, num, parseNum, pct, time } from '../format'
import { usePolling, type ChartColors } from '../hooks'
import { CfdChart } from './CfdChart'

const REFRESH_MS = 30_000
const ALL_TF: CfdAllTf[] = ['1m', '5m', '15m', '1h', '4h', '1d']
const TF_SHORT: Record<CfdAllTf, string> = { '1m': '1m', '5m': '5m', '15m': '15m', '1h': '1H', '4h': '4H', '1d': '1D' }

function stored<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T) || fallback
  } catch {
    return fallback
  }
}
function store(key: string, v: string) {
  try {
    localStorage.setItem(key, v)
  } catch {
    /* sin almacenamiento */
  }
}

function verdictClass(v: CfdVerdict): string {
  return v.startsWith('COMPRA') ? 'buy' : v.startsWith('VENTA') ? 'sell' : 'hold'
}
const VERDICT_ICON: Record<CfdVerdict, string> = {
  'COMPRA FUERTE': '▲▲', COMPRA: '▲', NEUTRAL: '●', VENTA: '▼', 'VENTA FUERTE': '▼▼', 'SIN DATOS': '—',
}

export function VerdictBadge({ v, big = false }: { v: CfdVerdict; big?: boolean }) {
  return (
    <span className={`badge ${verdictClass(v)}${big ? ' big' : ''}`}>
      <span aria-hidden="true">{VERDICT_ICON[v]}</span> {v}
    </span>
  )
}

function BiasCell({ b, tf }: { b: CfdBias | undefined; tf: CfdAllTf }) {
  const icon = !b || b.score == null ? '—' : b.bias === 'alcista' ? '▲' : b.bias === 'bajista' ? '▼' : '●'
  const cls = b?.bias === 'alcista' ? 'gain' : b?.bias === 'bajista' ? 'loss' : 'muted'
  const title = b?.score == null ? `${TF_SHORT[tf]}: ${b?.reason ?? 'sin datos'}` : `${TF_SHORT[tf]}: ${b.bias} (${num(b.score, 0)})`
  return <span className={`bias ${cls}`} title={title} aria-label={title}>{icon}</span>
}

function priceOf(r: CfdRow): { v: number | null; calibrated: boolean } {
  return r.xtb_price != null ? { v: r.xtb_price, calibrated: true } : { v: r.feed_price, calibrated: false }
}

function Scanner({ data, selected, onSelect }: { data: CfdOverview; selected: string; onSelect: (c: string) => void }) {
  return (
    <section className="card" aria-labelledby="cfd-scan-title">
      <div className="card-head">
        <h2 id="cfd-scan-title">Escáner · {data.tf_label}</h2>
        <span className="muted small">sesgo por temporalidad: ▲ alcista ▼ bajista ● neutral</span>
      </div>
      <div className="table-wrap">
        <table className="wl cfd-scan">
          <thead>
            <tr>
              <th>Activo</th>
              <th className="r">Precio</th>
              <th className="r">Día</th>
              <th className="c">{ALL_TF.map((t) => <span key={t} className="bias-h">{TF_SHORT[t]}</span>)}</th>
              <th className="r">ATR</th>
              <th className="r">Puntaje</th>
            </tr>
          </thead>
          <tbody>
            {data.assets.map((r) => {
              const p = priceOf(r)
              return (
                <tr key={r.code} className="row" aria-selected={r.code === selected} onClick={() => onSelect(r.code)}
                  tabIndex={0} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect(r.code)}>
                  <td>
                    <div className="sym">{r.code}</div>
                    <div className="name">{r.name}</div>
                  </td>
                  <td className="r num">
                    {num(p.v, r.digits)}
                    <div className="name">{p.calibrated ? 'xStation' : r.calibration.state === 'pendiente' ? 'calibrando…' : 'feed · sin calibrar'}</div>
                  </td>
                  <td className={`r num ${r.change_pct == null ? '' : r.change_pct >= 0 ? 'gain' : 'loss'}`}>{pct(r.change_pct, 2)}</td>
                  <td className="c nowrap">{ALL_TF.map((t) => <BiasCell key={t} b={r.bias[t]} tf={t} />)}</td>
                  <td className="r num">{num(r.atr, r.digits)}<div className="name">{pct(r.atr_pct, 2, false)}</div></td>
                  <td className="r">
                    <div className="num score-cell">{r.score == null ? '—' : `${r.score > 0 ? '+' : ''}${num(r.score, 0)}`}</div>
                    <VerdictBadge v={r.verdict} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function Field({ label, value, onChange, suffix, hint, step = 'any' }: {
  label: string; value: string; onChange: (v: string) => void; suffix?: string; hint?: string; step?: string
}) {
  return (
    <label className="cfd-field">
      <span>{label}</span>
      <span className="cfd-input">
        <input className="input num" inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value)} step={step} />
        {suffix && <span className="muted small">{suffix}</span>}
      </span>
      {hint && <span className="muted small">{hint}</span>}
    </label>
  )
}

function SettingsCard({ s, contracts, telegram, onSaved }: {
  s: CfdSettings; contracts: Record<string, CfdContract>; telegram: boolean; onSaved: () => void
}) {
  const [form, setForm] = useState(() => ({
    capital_usd: String(s.capital_usd), risk_pct: String(s.risk_pct), sl_atr: String(s.sl_atr), tp_atr: String(s.tp_atr),
    alert_threshold: String(s.alert_threshold),
  }))
  const [alerts, setAlerts] = useState(s.alerts_enabled)
  const [contract, setContract] = useState<Record<string, { value_per_point: string; spread_points: string; margin_pct: string }>>(() =>
    Object.fromEntries(Object.entries(contracts).map(([c, k]) => [c, {
      value_per_point: String(k.value_per_point), spread_points: String(k.spread_points), margin_pct: String(k.margin_pct),
    }])))
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof form) => (v: string) => setForm((f) => ({ ...f, [k]: v }))
  const risk = (parseNum(form.capital_usd) ?? 0) * (parseNum(form.risk_pct) ?? 0) / 100

  async function save(e: FormEvent) {
    e.preventDefault()
    setMsg(null)
    setErr(null)
    const patch: Record<string, unknown> = { alerts_enabled: alerts }
    for (const [k, v] of Object.entries(form)) {
      const n = parseNum(v)
      if (n == null || n <= 0) {
        setErr('Revisa los números: deben ser mayores que 0.')
        return
      }
      patch[k] = n
    }
    const assets: Record<string, Record<string, number>> = {}
    for (const [code, c] of Object.entries(contract)) {
      const vpp = parseNum(c.value_per_point)
      const sp = parseNum(c.spread_points)
      const mg = parseNum(c.margin_pct)
      if (vpp == null || vpp <= 0 || sp == null || sp < 0 || mg == null || mg <= 0) {
        setErr(`Revisa el contrato de ${code}.`)
        return
      }
      assets[code] = { value_per_point: vpp, spread_points: sp, margin_pct: mg }
    }
    patch.assets = assets
    setBusy(true)
    try {
      await api.cfdSaveSettings(patch as Partial<CfdSettings>)
      setMsg('Guardado.')
      onSaved()
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card" aria-labelledby="cfd-set-title">
      <div className="card-head"><h2 id="cfd-set-title">Gestión de riesgo</h2></div>
      <form onSubmit={save} className="cfd-form">
        <Field label="Capital de la cuenta" value={form.capital_usd} onChange={set('capital_usd')} suffix="USD" />
        <Field label="Riesgo por operación" value={form.risk_pct} onChange={set('risk_pct')} suffix="%"
          hint={`= ${num(risk, 2)} USD por operación${(parseNum(form.risk_pct) ?? 0) > 2 ? ' · más de 2 % es agresivo' : ''}`} />
        <Field label="Stop (SL)" value={form.sl_atr} onChange={set('sl_atr')} suffix="× ATR" />
        <Field label="Objetivo (TP)" value={form.tp_atr} onChange={set('tp_atr')} suffix="× ATR" />
        <label className="cfd-check">
          <input type="checkbox" checked={alerts} onChange={(e) => setAlerts(e.target.checked)} />
          <span>Alertas por Telegram cuando el puntaje llegue a ±</span>
          <input className="input num tiny" inputMode="decimal" value={form.alert_threshold} onChange={(e) => set('alert_threshold')(e.target.value)} aria-label="Umbral de alerta" />
        </label>
        {!telegram && <p className="muted small" style={{ margin: 0 }}>Telegram no está configurado: agrega TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en .env. Las alertas igual quedan en la pestaña Alertas.</p>}
        <details className="cfd-adv">
          <summary>Contrato por activo (valor del punto, spread, margen)</summary>
          <p className="muted small">Valores de la tabla de especificaciones de XTB. Si XTB los cambia, corrígelos aquí. El spread (en puntos) se suma al riesgo.</p>
          <div className="table-wrap">
            <table className="bt">
              <thead><tr><th>Activo</th><th>USD por punto (1 lote)</th><th>Spread (pts)</th><th>Margen %</th></tr></thead>
              <tbody>
                {Object.entries(contract).map(([code, c]) => (
                  <tr key={code}>
                    <td>{code}</td>
                    {(['value_per_point', 'spread_points', 'margin_pct'] as const).map((k) => (
                      <td key={k}>
                        <input className="input num tiny" inputMode="decimal" value={c[k]} aria-label={`${code} ${k}`}
                          onChange={(e) => setContract((all) => ({ ...all, [code]: { ...all[code], [k]: e.target.value } }))} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
        <div className="row-actions">
          <button className="btn primary" disabled={busy}>{busy ? 'Guardando…' : 'Guardar'}</button>
          {msg && <span className="muted small" role="status">{msg}</span>}
          {err && <span className="error" role="alert">{err}</span>}
        </div>
      </form>
    </section>
  )
}

function ticketText(r: CfdRow, t: CfdTicket): string {
  const d = r.digits
  return [
    `${r.code} · ${t.side} · ${t.lots?.toFixed(2)} lotes`,
    `Entrada ≈ ${t.entry?.toFixed(d)}`,
    `Stop Loss: ${t.sl?.toFixed(d)} (${t.sl_points?.toFixed(d)} pts)`,
    `Take Profit: ${t.tp?.toFixed(d)} (${t.tp_points?.toFixed(d)} pts)`,
    `Riesgo ${t.risk_usd?.toFixed(2)} USD · objetivo ${t.reward_usd?.toFixed(2)} USD · R:R ${t.rr?.toFixed(2)}`,
  ].join('\n')
}

function TicketCard({ r }: { r: CfdRow }) {
  const t = r.ticket
  const d = r.digits
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(ticketText(r, t))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      /* portapapeles bloqueado */
    }
  }
  const dir = t.direction === 'LARGO' ? 'buy' : 'sell'
  return (
    <section className={`card ticket ${t.actionable ? dir : 'idle'}`} aria-labelledby="ticket-title">
      <div className="card-head">
        <h2 id="ticket-title">Ticket listo para XTB</h2>
        {t.entry != null && <button type="button" className="btn" onClick={copy}>{copied ? '✓ Copiado' : 'Copiar'}</button>}
      </div>
      {!t.actionable && (
        <p className="warn" style={{ marginTop: 0 }}>
          {r.verdict === 'NEUTRAL' || r.verdict === 'SIN DATOS'
            ? `Sin señal (${r.verdict.toLowerCase()}): no hay operación sugerida. Los niveles de abajo son solo de referencia para un ${t.direction === 'LARGO' ? 'largo' : 'corto'}.`
            : t.error ?? 'No se puede armar el ticket.'}
          {(r.verdict === 'NEUTRAL' || r.verdict === 'SIN DATOS') && t.error && <><br />{t.error}</>}
        </p>
      )}
      {t.entry != null ? (
        <>
          <div className="ticket-side">
            <span className={`badge big ${t.actionable ? dir : 'hold'}`}>{t.direction === 'LARGO' ? '▲' : '▼'} {t.side}</span>
            <span className="ticket-lots num"><b>{t.lots ? num(t.lots, 2) : '—'}</b> lotes</span>
          </div>
          <dl className="ticket-grid num">
            <dt>Entrada (precio actual)</dt><dd>≈ {num(t.entry, d)}</dd>
            <dt>Stop Loss</dt><dd className="loss">{num(t.sl, d)} <span className="muted small">({num(t.sl_points, d)} pts)</span></dd>
            <dt>Take Profit</dt><dd className="gain">{num(t.tp, d)} <span className="muted small">({num(t.tp_points, d)} pts)</span></dd>
            {t.lots ? <>
            <dt>Riesgo real</dt><dd>{num(t.risk_usd, 2)} USD <span className="muted small">({num(t.risk_pct_real, 2)} % · máx. {num(t.risk_budget_usd, 2)})</span></dd>
            <dt>Objetivo</dt><dd>{num(t.reward_usd, 2)} USD <span className="muted small">(R:R {num(t.rr, 2)})</span></dd>
            <dt>Valor por punto</dt><dd>{num(t.usd_per_point, 2)} USD <span className="muted small">({num(t.value_per_point, 0)} × {num(t.lots, 2)} lotes)</span></dd>
            <dt>Margen aprox.</dt><dd>{num(t.margin_usd, 2)} USD <span className="muted small">({num(t.margin_pct, 0)} % de {num(t.notional_usd, 0)})</span></dd>
            </> : <>
            <dt>Lote mínimo ({num(t.min_lot, 2)})</dt><dd>arriesga {num(t.min_lot_risk_usd, 2)} USD <span className="muted small">(máx. {num(t.risk_budget_usd, 2)})</span></dd>
            </>}
          </dl>
          {t.warnings.map((w) => <p key={w} className="warn small">⚠ {w}</p>)}
          {t.actionable && t.error && <p className="error">{t.error}</p>}
          <p className="muted small" style={{ marginBottom: 0 }}>
            En xStation: {t.direction === 'LARGO' ? 'BUY' : 'SELL'} {r.code}, volumen {t.lots ? num(t.lots, 2) : '—'}, y fija SL y TP en
            los precios de arriba. Si el precio de xStation ya se movió, conserva las <b>distancias</b> en puntos desde tu entrada real.
          </p>
        </>
      ) : (
        <p className="muted">{t.error ?? 'Sin datos.'}</p>
      )}
    </section>
  )
}

function CalibrationCard({ r, onDone }: { r: CfdRow; onDone: () => void }) {
  const [priceIn, setPriceIn] = useState('')
  const [offsetIn, setOffsetIn] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const c = r.calibration
  async function run(body: { xtb_price?: number; manual_offset?: number }) {
    setErr(null)
    setBusy(true)
    try {
      await api.cfdCalibrate({ asset: r.code, ...body, at: new Date().toISOString() })
      setPriceIn('')
      setOffsetIn('')
      onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  function submit(e: FormEvent) {
    e.preventDefault()
    const p = parseNum(priceIn)
    if (p == null || p <= 0) return setErr('Escribe el precio que ves ahora en xStation.')
    void run({ xtb_price: p })
  }
  function submitOffset(e: FormEvent) {
    e.preventDefault()
    const o = parseNum(offsetIn)
    if (o == null) return setErr('Escribe el desfase (puede ser negativo).')
    void run({ manual_offset: o })
  }
  const cls = c.state === 'ok' ? 'st-ok' : c.state === 'pendiente' ? 'st-review' : c.state === 'sin calibrar' ? 'st-none' : 'st-sell'
  return (
    <section className="card" aria-labelledby="cal-title">
      <div className="card-head">
        <h2 id="cal-title">Calibrar precio con xStation</h2>
        <span className={`status ${cls}`}>{c.state}</span>
      </div>
      <p className="small" style={{ marginTop: 0 }}>{c.text}</p>
      <p className="muted small">
        Fuente: {r.source_name ?? '—'}{r.feed_symbol ? ` (${r.feed_symbol})` : ''} · precio del feed {num(r.feed_price, r.digits)}
        {c.offset != null && <> · desfase <b className="num">{c.offset > 0 ? '+' : ''}{num(c.offset, r.digits)}</b></>}
        {c.at && <> · calibrado {dateTime(c.at)}</>}
      </p>
      <form className="cfd-cal" onSubmit={submit}>
        <label className="cfd-field grow">
          <span>Precio que ves AHORA en xStation (BID, el de los gráficos)</span>
          <input className="input num" inputMode="decimal" value={priceIn} onChange={(e) => setPriceIn(e.target.value)} placeholder={num(r.feed_price, r.digits)} />
        </label>
        <button className="btn primary" disabled={busy}>Calibrar</button>
      </form>
      <details className="cfd-adv">
        <summary>Escribir el desfase a mano / borrar</summary>
        <form className="cfd-cal" onSubmit={submitOffset}>
          <label className="cfd-field grow">
            <span>Desfase = precio xStation − precio del feed</span>
            <input className="input num" inputMode="decimal" value={offsetIn} onChange={(e) => setOffsetIn(e.target.value)} />
          </label>
          <button className="btn" disabled={busy}>Usar</button>
          <button type="button" className="btn" disabled={busy}
            onClick={async () => { await api.cfdClearCalibration(r.code); onDone() }}>Borrar calibración</button>
        </form>
      </details>
      {err && <p className="error" role="alert">{err}</p>}
      <p className="muted small" style={{ marginBottom: 0 }}>
        El feed gratuito llega con ~10 min de retraso: el desfase se calcula contra la vela del feed de la hora en que
        escribiste el precio, por eso puede quedar «pendiente» unos minutos. Recalibra cada día y tras el vencimiento
        de un futuro (el desfase salta).
      </p>
    </section>
  )
}

function Breakdown({ d }: { d: CfdDetail }) {
  const groups = useMemo(() => {
    const m = new Map<string, CfdRule[]>()
    for (const r of d.rules) m.set(r.group, [...(m.get(r.group) ?? []), r])
    return [...m.entries()]
  }, [d.rules])
  return (
    <section className="card" aria-labelledby="bd-title">
      <div className="card-head">
        <h2 id="bd-title">Por qué este puntaje</h2>
        <span className="muted small">
          {d.mtf.map((m) => `${m.label} ${Math.round(m.weight * 100)} %`).join(' + ')} · técnico {d.base_score == null ? '—' : num(d.base_score, 0)}
          {' '}+ intermercado = <b>{d.score == null ? '—' : num(d.score, 0)}</b>
        </span>
      </div>
      <div className="rules">
        {groups.map(([g, rules]) => (
          <div key={g} className="rule-group">
            <h3>{g}</h3>
            <ul>
              {rules.map((r) => (
                <li key={r.id} className={r.available ? '' : 'muted'}>
                  <span className={`rule-ico ${!r.available ? 'muted' : r.agrees ? 'gain' : r.value === 0 ? 'muted' : 'loss'}`} aria-hidden="true">
                    {!r.available ? '○' : r.agrees ? '✓' : r.value === 0 ? '●' : '✗'}
                  </span>
                  <span className="rule-text">
                    {r.label}
                    <span className="muted small"> · {r.detail}</span>
                  </span>
                  <span className={`num rule-pts ${r.points > 0 ? 'gain' : r.points < 0 ? 'loss' : 'muted'}`}>
                    {r.points > 0 ? '+' : ''}{num(r.points, 1)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <p className="muted small" style={{ marginBottom: 0 }}>
        ✓ a favor de la dirección del puntaje · ✗ en contra · ● neutral · ○ sin datos suficientes. Se usan solo velas cerradas.
      </p>
    </section>
  )
}

function DetailPanel({ code, tf, colors }: { code: string; tf: CfdTf; colors: ChartColors }) {
  const det = usePolling(() => api.cfdDetail(code, tf), REFRESH_MS, [code, tf])
  const d = det.data
  const t = d?.ticket
  const levels = useMemo(
    () => (t && t.entry != null ? { entry: t.entry, sl: t.sl, tp: t.tp } : null),
    [t?.entry, t?.sl, t?.tp], // eslint-disable-line react-hooks/exhaustive-deps
  )
  if (det.error && !d) return <section className="card"><p className="error">{det.error}</p></section>
  if (!d) return <section className="card"><p className="muted">Cargando {code}…</p></section>
  return (
    <div className="col">
      <section className="card" aria-labelledby="cfd-hero">
        <div className="hero-title">
          <h2 id="cfd-hero">{d.code}</h2>
          <span className="muted">{d.name}</span>
          <span className="chip">{d.tf_label}</span>
          <VerdictBadge v={d.verdict} big />
          <span className="score-big num">{d.score == null ? '—' : `${d.score > 0 ? '+' : ''}${num(d.score, 0)}`}</span>
        </div>
        <div className="hero">
          <span className="price num">{num(priceOf(d).v, d.digits)}</span>
          <span className={`chg ${d.change_pct == null ? '' : d.change_pct >= 0 ? 'gain' : 'loss'}`}>{pct(d.change_pct, 2)}</span>
          <span className="muted small">{priceOf(d).calibrated ? 'precio xStation (calibrado)' : 'precio del feed, sin calibrar'}</span>
        </div>
        <p className="muted small" style={{ margin: '4px 0 0' }}>
          {d.source_name} {d.feed_symbol} · última vela {time(d.last_bar)}
          {d.delay_min != null && <> · <b className={d.delay_min > 3 ? 'warn-text' : ''}>~{num(d.delay_min, 0)} min de retraso</b></>}
          {!d.market_open && ' · mercado cerrado'}
          {d.reason && <span className="error"> · {d.reason}</span>}
        </p>
        {[...d.errors, ...d.notes].slice(0, 3).map((e) => <p key={e} className="muted small" style={{ margin: '2px 0 0' }}>⚠ {e}</p>)}
      </section>

      <section className="card" aria-labelledby="cfd-chart-title">
        <div className="card-head">
          <h2 id="cfd-chart-title">Velas {d.tf_label}</h2>
          <span className="muted small">{d.chart.calibrated ? 'en precio de xStation' : 'en precio del feed (calibra para ver precios de xStation)'}</span>
        </div>
        {d.chart.bars.length ? (
          <CfdChart bars={d.chart.bars} digits={d.digits} colors={colors} levels={levels} resetKey={`${code}-${tf}`} intraday={tf !== '4h'} />
        ) : <p className="muted">Sin velas para mostrar.</p>}
      </section>

      <div className="cfd-two">
        <TicketCard r={d} />
        <CalibrationCard r={d} onDone={det.reload} />
      </div>
      <Breakdown d={d} />
    </div>
  )
}

export function CfdView({ colors }: { colors: ChartColors }) {
  const [tf, setTf] = useState<CfdTf | null>(() => (stored('cfd-tf', '') as CfdTf) || null)
  const [selected, setSelected] = useState<string>(() => stored('cfd-asset', 'GOLD'))
  const ov = usePolling(() => api.cfdOverview(tf ?? undefined), REFRESH_MS, [tf])
  const data = ov.data
  const activeTf = tf ?? data?.tf ?? '5m'
  useEffect(() => store('cfd-asset', selected), [selected])

  async function chooseTf(next: CfdTf) {
    setTf(next)
    store('cfd-tf', next)
    try {
      await api.cfdSaveSettings({ timeframe: next }) // las alertas usan la temporalidad elegida
    } catch {
      /* se reintenta al guardar los ajustes */
    }
  }

  const delay = data?.assets.map((a) => a.delay_min).filter((x): x is number => x != null)
  const maxDelay = delay?.length ? Math.max(...delay) : null
  const rg = data?.regime

  return (
    <div className="page cfd">
      <p className="warn cfd-risk" role="note">
        ⚠ {data?.disclaimer ?? 'Herramienta educativa, no es asesoría financiera. El copiloto no ejecuta órdenes.'}
        {maxDelay != null && maxDelay > 2 && <> <b>Datos con ~{num(maxDelay, 0)} min de retraso</b>: en 1 y 5 minutos confirma siempre el precio en xStation antes de operar.</>}
      </p>
      <section className="card cfd-top">
        <div className="seg" role="group" aria-label="Temporalidad">
          {(data?.timeframes ?? ['1m', '5m', '15m', '1h', '4h']).map((t) => (
            <button key={t} type="button" aria-pressed={activeTf === t} onClick={() => chooseTf(t)}>{TF_SHORT[t]}</button>
          ))}
        </div>
        {rg && (
          <span className={`chip regime ${rg.state}`} title={rg.detail}>
            {rg.state === 'risk-on' ? '▲' : rg.state === 'risk-off' ? '▼' : '●'} {rg.label} · amplitud {rg.breadth}
          </span>
        )}
        {data && (
          <span className="chip"><span className={`dot${data.market_open ? ' on' : ''}`} aria-hidden="true" />
            {data.market_open ? 'Mercado abierto' : 'Mercado cerrado'}
          </span>
        )}
        {data && (
          <span className="chip" title={data.feed.twelvedata_last_error ?? ''}>
            Datos: {data.feed.twelvedata_configured ? `Twelve Data (oro y plata, ${data.feed.twelvedata_usage.last_day}/${data.feed.twelvedata_usage.per_day} hoy) + Yahoo` : 'Yahoo (futuros)'}
          </span>
        )}
        {data && <span className="chip">Telegram: {data.settings.alerts_enabled ? (data.alerts.telegram ? 'activo' : 'sin configurar') : 'apagado'}</span>}
        {ov.loading && <span className="muted small">Actualizando…</span>}
      </section>
      {ov.error && !data && <section className="card"><p className="error">{ov.error}</p></section>}
      {!data && !ov.error && <section className="card"><p className="muted">Cargando los 6 CFD (la primera vez tarda unos segundos)…</p></section>}
      {data && <Scanner data={data} selected={selected} onSelect={setSelected} />}
      {data && (
        <div className="cfd-layout">
          <DetailPanel key={`${selected}-${activeTf}`} code={selected} tf={activeTf} colors={colors} />
          <div className="col">
            <SettingsCard key={JSON.stringify(data.settings)} s={data.settings} contracts={data.contracts}
              telegram={data.alerts.telegram} onSaved={ov.reload} />
          </div>
        </div>
      )}
    </div>
  )
}
