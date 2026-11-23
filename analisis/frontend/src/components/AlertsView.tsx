import { useState } from 'react'
import { api, LOCAL_MODE } from '../api'
import { dateTime } from '../format'
import type { AlertsResponse, Severity } from '../types'

export const SEVERITY: Record<Severity, { icon: string; label: string; cls: string }> = {
  critical: { icon: '⛔', label: 'Crítica', cls: 'st-sell' },
  warning: { icon: '⚠', label: 'Atención', cls: 'st-review' },
  info: { icon: 'ℹ', label: 'Info', cls: 'st-none' },
}

const KIND: Record<string, string> = {
  venta: 'Caída: vender', revision: 'Caída: revisar', senal: 'Cambio de señal', datos: 'Datos', prueba: 'Prueba',
}

export function AlertsView({ data, onChanged, onSelect }: {
  data: AlertsResponse | null
  onChanged: () => void
  onSelect: (s: string) => void
}) {
  const [msg, setMsg] = useState<string | null>(null)

  async function test() {
    setMsg(null)
    try {
      const r = await api.testAlert()
      const sent = Array.isArray(r.sent) ? r.sent : r.sent ? [r.sent] : []
      setMsg(sent.length ? `Enviada por ${sent.join(' y ').replace('telefono', 'notificación del teléfono')}.` + (r.errors.length ? ` Fallas: ${r.errors.join('; ')}` : '')
        : r.errors.length ? `No se pudo enviar: ${r.errors.join('; ')}`
          : LOCAL_MODE ? 'Guardada en la app. Para recibir notificaciones, permite las notificaciones de la app en Android.'
            : 'Guardada en la app. No hay Telegram ni email configurados en .env.')
      onChanged()
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e))
    }
  }

  const ch = data?.channels
  return (
    <section className="card" aria-labelledby="al-title">
      <div className="card-head">
        <h2 id="al-title">Alertas</h2>
        <div className="chips">
          <span className="chip">En la app ✓</span>
          {LOCAL_MODE ? (
            <span className="chip">Notificaciones del teléfono {ch?.phone ? '✓' : '— sin permiso'}</span>
          ) : (
            <>
              <span className="chip">Telegram {ch?.telegram ? '✓' : '— sin configurar'}</span>
              <span className="chip">Email {ch?.email ? '✓' : '— sin configurar'}</span>
            </>
          )}
          <button className="btn" onClick={test}>Enviar prueba</button>
          <button className="btn" disabled={!data?.unread} onClick={async () => { await api.readAllAlerts(); onChanged() }}>
            Marcar todas como leídas
          </button>
        </div>
      </div>
      {msg && <p className="small">{msg}</p>}
      <p className="muted small" style={{ margin: '0 0 8px' }}>
        Se generan solas en cada actualización: caída desde tu costo de compra (revisar / vender), cambios de señal y
        precios que dejan de llegar.{' '}
        {LOCAL_MODE
          ? 'Las importantes llegan como notificación mientras la app está abierta o en segundo plano reciente.'
          : 'Telegram y email se activan con las variables de .env (ver README).'}
      </p>
      {!data && <p className="muted">Cargando…</p>}
      {data && data.alerts.length === 0 && <p className="muted">No hay alertas.</p>}
      <ul className="alerts">
        {data?.alerts.map((a) => {
          const s = SEVERITY[a.severity]
          return (
            <li key={a.id} className={a.read ? 'read' : ''}>
              <span className={`status ${s.cls}`}><span aria-hidden="true">{s.icon}</span>{s.label}</span>
              <div className="alert-body">
                <div className="alert-title">{a.title}</div>
                <div className="small">{a.message}</div>
                <div className="small muted">
                  {KIND[a.kind] ?? a.kind} · {dateTime(a.created_at)}
                  {a.resolved_at ? ` · resuelta ${dateTime(a.resolved_at)}` : a.kind === 'venta' || a.kind === 'revision' || a.kind === 'datos' ? ' · vigente' : ''}
                  {a.notified.length ? ` · enviada por ${a.notified.join(', ')}` : ''}
                  {a.notify_error ? ` · error al enviar: ${a.notify_error}` : ''}
                </div>
              </div>
              <div className="alert-actions">
                {a.symbol && <button className="btn" onClick={() => onSelect(a.symbol!)}>Ver {a.symbol}</button>}
                {!a.read && (
                  <button className="btn" onClick={async () => { await api.readAlert(a.id); onChanged() }}>Leída</button>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
