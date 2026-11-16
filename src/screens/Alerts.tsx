import { useState, type FormEvent } from 'react'
import { Field, go } from '../components'
import { serverWatches } from '../lib/alerts'
import { parseNum } from '../lib/csv'
import { describeRule, fmtDate, fmtPct, fmtPrice, kindLabel } from '../lib/format'
import { uid, useApp, type AlertSync } from '../store'
import type { AlertEvent, AlertKind, AlertRule, Asset } from '../types'

/** Valor con que se cumplió una alerta: el servidor lo envía ya formateado; las locales se formatean aquí. */
export function eventValue(e: AlertEvent, a: Asset | undefined): string {
  if (e.valueText) return e.valueText
  return e.message.startsWith('Variación') ? fmtPct(e.value) : fmtPrice(e.value, a?.currency ?? 'USD')
}

const SYNC_TEXT: Record<AlertSync, string> = {
  off: '',
  pendiente: 'Enviando las reglas al servidor…',
  ok: 'Reglas sincronizadas: el servidor las revisa cada 5 minutos en horario de mercado.',
  error: 'No se pudo enviar las reglas al servidor. Se reintentará al abrir la app o al cambiar una regla.',
  ajeno: 'El servidor de alertas está vinculado a otra instalación de la app: estas reglas no se están vigilando.',
}

export function Alerts() {
  const { state, asset, saveAlert, deleteAlert, alertSync } = useApp()
  const push = state.settings.notifyPush
  return (
    <>
      <button className="btn primary" onClick={() => go('alerta/nueva')}>Nueva alerta</button>

      {alertSync !== 'off' && <p className={`small ${alertSync === 'error' || alertSync === 'ajeno' ? 'loss' : 'muted'}`}>{SYNC_TEXT[alertSync]}</p>}
      {alertSync !== 'off' && !push && (
        <p className="warn">
          Las notificaciones push están desactivadas: las alertas cumplidas solo se verán al abrir la app.{' '}
          <button className="link" onClick={() => go('ajustes')}>Activarlas en Ajustes</button>
        </p>
      )}

      <h2>Reglas ({state.alerts.length})</h2>
      <section className="card list">
        {state.alerts.length === 0 && <p className="muted">Aún no tienes reglas.</p>}
        {state.alerts.map((r) => {
          const a = asset(r.assetId)
          // Sin servidor para este activo (precio manual o datos de ejemplo), la regla solo se revisa con la app abierta.
          const local = state.demo || !serverWatches(a)
          return (
            <div key={r.id} className={`row ${r.muted ? 'dim' : ''}`}>
              <div>
                <b>{r.assetId}</b> · {describeRule(r, a?.currency ?? 'USD')}
                <div className="muted">
                  {r.muted ? 'Silenciada' : local ? 'Se revisa solo con la app abierta' : push ? 'Avisa por notificación push' : 'Vigilada por el servidor, sin aviso'}
                </div>
                {local && !r.muted && (r.kind === 'cruce_medias' || r.kind === 'indicador') && (
                  <div className="loss small">Este tipo de regla necesita un activo con precio automático.</div>
                )}
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
        {state.events.map((e) => (
          <div key={e.id} className="row">
            <div>
              <b>{e.assetId}</b> · {e.message}
              <div className="muted">{fmtDate(e.triggeredAt, true)}</div>
            </div>
            <div className="right">
              <div className="label">Valor</div>
              <div className="num">{eventValue(e, asset(e.assetId))}</div>
            </div>
          </div>
        ))}
      </section>
      <p className="foot muted">Cada regla avisa a lo más una vez por día. Las alertas solo informan: nunca ejecutan órdenes.</p>
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

  const rule: AlertRule = {
    id: r.id, assetId: r.assetId, kind: r.kind, channels: ['push'], muted: r.muted, createdAt: r.createdAt,
    ...(r.kind === 'precio_sobre' || r.kind === 'precio_bajo' ? { level: n('level') } : {}),
    ...(r.kind === 'variacion_dia' ? { pct: n('pct') } : {}),
    ...(r.kind === 'cruce_medias' ? { fast: n('fast'), slow: n('slow') } : {}),
    ...(r.kind === 'indicador' ? { indicator: r.indicator ?? 'RSI', op: r.op ?? 'menor', threshold: n('threshold') } : {}),
  }
  const mediasOk = rule.kind !== 'cruce_medias' || (!!rule.fast && !!rule.slow && rule.fast < rule.slow && rule.slow <= 200)
  const complete =
    !!r.assetId && mediasOk &&
    ((rule.level ?? rule.pct ?? rule.threshold) !== undefined || (rule.fast !== undefined && rule.slow !== undefined))
  const needsAuto = (r.kind === 'cruce_medias' || r.kind === 'indicador') && !!a && (state.demo || !serverWatches(a))

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
        <>
          <div className="grid2">
            {input('fast', 'Media corta (días)')}
            {input('slow', 'Media larga (días)')}
          </div>
          <p className={`small ${mediasOk ? 'muted' : 'loss'}`}>La corta debe ser menor que la larga, y la larga de 200 días como máximo. Avisa el día en que se cruzan, al alza o a la baja.</p>
        </>
      )}
      {r.kind === 'indicador' && (
        <>
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
          <p className="muted small">RSI de 14 días; MACD 12/26, con cierres diarios. Avisa el día en que el indicador cruza ese valor.</p>
        </>
      )}
      {needsAuto && <p className="loss small">Este tipo de regla necesita un activo con precio automático: con datos de ejemplo o precio manual no se evaluará.</p>}

      <button className="btn primary" disabled={!complete}>{existing ? 'Guardar cambios' : 'Crear alerta'}</button>
      <p className="muted small">El aviso llega por notificación push. La alerta solo te avisa: nunca ejecuta una orden.</p>
    </form>
  )
}
