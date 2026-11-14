import { useState, type FormEvent } from 'react'
import { Field, go } from '../components'
import { parseNum } from '../lib/csv'
import { channelLabel, describeRule, fmtDate, fmtPct, fmtPrice, kindLabel } from '../lib/format'
import { uid, useApp } from '../store'
import type { AlertKind, AlertRule, Channel } from '../types'

export function Alerts() {
  const { state, asset, saveAlert, deleteAlert } = useApp()
  return (
    <>
      <button className="btn primary" onClick={() => go('alerta/nueva')}>Nueva alerta</button>

      <h2>Reglas ({state.alerts.length})</h2>
      <section className="card list">
        {state.alerts.length === 0 && <p className="muted">Aún no tienes reglas.</p>}
        {state.alerts.map((r) => {
          const a = asset(r.assetId)
          return (
            <div key={r.id} className={`row ${r.muted ? 'dim' : ''}`}>
              <div>
                <b>{r.assetId}</b> · {describeRule(r, a?.currency ?? 'USD')}
                <div className="muted">
                  {r.channels.map((c) => channelLabel[c]).join(', ')}
                  {r.muted && ' · silenciada'}
                </div>
                <div className="actions">
                  <button className="link" onClick={() => saveAlert({ ...r, muted: !r.muted })}>{r.muted ? 'Reactivar' : 'Silenciar'}</button>
                  <button className="link" onClick={() => go(`alerta/${r.id}`)}>Editar</button>
                  <button className="link danger" onClick={() => confirm('¿Eliminar esta alerta?') && deleteAlert(r.id)}>Eliminar</button>
                </div>
              </div>
            </div>
          )
        })}
      </section>

      <h2>Alertas cumplidas</h2>
      <section className="card list">
        {state.events.length === 0 && <p className="muted">Ninguna todavía.</p>}
        {state.events.map((e) => {
          const a = asset(e.assetId)
          const isPct = e.message.startsWith('Variación')
          return (
            <div key={e.id} className="row">
              <div>
                <b>{e.assetId}</b> · {e.message}
                <div className="muted">{fmtDate(e.triggeredAt, true)}</div>
              </div>
              <div className="right">
                <div className="label">Valor</div>
                <div className="num">{isPct ? fmtPct(e.value) : fmtPrice(e.value, a?.currency ?? 'USD')}</div>
              </div>
            </div>
          )
        })}
      </section>
      <p className="foot muted">Las alertas solo informan. Nunca ejecutan órdenes.</p>
    </>
  )
}

export function AlertForm({ id, assetId = '' }: { id?: string; assetId?: string }) {
  const { state, assets, asset, saveAlert } = useApp()
  const existing = state.alerts.find((a) => a.id === id)
  const [r, setR] = useState<AlertRule>(
    existing ?? { id: uid(), assetId, kind: 'precio_sobre', channels: ['push'], muted: false, createdAt: new Date().toISOString() },
  )
  const [num, setNum] = useState({
    level: String(existing?.level ?? ''),
    pct: String(existing?.pct ?? 3),
    fast: String(existing?.fast ?? 50),
    slow: String(existing?.slow ?? 200),
    threshold: String(existing?.threshold ?? 30),
  })
  const n = (k: keyof typeof num) => parseNum(num[k]) ?? undefined
  const input = (k: keyof typeof num, label: string) => (
    <Field label={label}>
      <input inputMode="decimal" value={num[k]} onChange={(e) => setNum({ ...num, [k]: e.target.value })} required />
    </Field>
  )
  const a = asset(r.assetId)
  const q = state.quotes[r.assetId]
  const toggle = (c: Channel) =>
    setR({ ...r, channels: r.channels.includes(c) ? r.channels.filter((x) => x !== c) : [...r.channels, c] })

  const rule: AlertRule = {
    id: r.id, assetId: r.assetId, kind: r.kind, channels: r.channels, muted: r.muted, createdAt: r.createdAt,
    ...(r.kind === 'precio_sobre' || r.kind === 'precio_bajo' ? { level: n('level') } : {}),
    ...(r.kind === 'variacion_dia' ? { pct: n('pct') } : {}),
    ...(r.kind === 'cruce_medias' ? { fast: n('fast'), slow: n('slow') } : {}),
    ...(r.kind === 'indicador' ? { indicator: r.indicator ?? 'RSI', op: r.op ?? 'menor', threshold: n('threshold') } : {}),
  }
  const complete =
    !!r.assetId && r.channels.length > 0 &&
    ((rule.level ?? rule.pct ?? rule.threshold) !== undefined || (rule.fast !== undefined && rule.slow !== undefined))

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!complete) return
    saveAlert(rule)
    go('alertas')
  }

  return (
    <form className="card form" onSubmit={submit}>
      <Field label="Activo">
        <select value={r.assetId} onChange={(e) => setR({ ...r, assetId: e.target.value })} required>
          <option value="" disabled>Elegir…</option>
          {assets.map((x) => <option key={x.id} value={x.id}>{x.id} — {x.name}</option>)}
        </select>
      </Field>
      {a && q && <div className="muted num">Precio actual: {fmtPrice(q.price, a.currency)}</div>}
      <Field label="Avisarme cuando">
        <select value={r.kind} onChange={(e) => setR({ ...r, kind: e.target.value as AlertKind })}>
          {(Object.keys(kindLabel) as AlertKind[]).map((k) => <option key={k} value={k}>{kindLabel[k]}</option>)}
        </select>
      </Field>

      {(r.kind === 'precio_sobre' || r.kind === 'precio_bajo') && input('level', `Nivel de precio${a ? ` (${a.currency})` : ''}`)}
      {r.kind === 'variacion_dia' && input('pct', 'Sube o baja en el día al menos (%)')}
      {r.kind === 'cruce_medias' && (
        <div className="grid2">
          {input('fast', 'Media corta (días)')}
          {input('slow', 'Media larga (días)')}
        </div>
      )}
      {r.kind === 'indicador' && (
        <div className="grid3">
          <Field label="Indicador">
            <select value={r.indicator ?? 'RSI'} onChange={(e) => setR({ ...r, indicator: e.target.value as 'RSI' | 'MACD' })}>
              <option>RSI</option>
              <option>MACD</option>
            </select>
          </Field>
          <Field label="Condición">
            <select value={r.op ?? 'menor'} onChange={(e) => setR({ ...r, op: e.target.value as 'menor' | 'mayor' })}>
              <option value="menor">Menor que</option>
              <option value="mayor">Mayor que</option>
            </select>
          </Field>
          {input('threshold', 'Valor')}
        </div>
      )}

      <fieldset>
        <legend>Avisar por</legend>
        {(Object.keys(channelLabel) as Channel[]).map((c) => (
          <label key={c} className="check">
            <input type="checkbox" checked={r.channels.includes(c)} onChange={() => toggle(c)} /> {channelLabel[c]}
          </label>
        ))}
      </fieldset>

      <button className="btn primary" disabled={!complete}>{existing ? 'Guardar cambios' : 'Crear alerta'}</button>
      <p className="muted small">La alerta solo te avisa. Nunca ejecuta una orden.</p>
    </form>
  )
}
