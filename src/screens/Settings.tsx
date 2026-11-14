import { useState, type FormEvent } from 'react'
import { Field, Seg, go } from '../components'
import { PIN_HINT, PIN_RULE, PinInput } from '../Gate'
import { emptyState, useApp } from '../store'
import type { Settings as S } from '../types'

export function Settings() {
  const { state, setSettings, security, notify, replaceAll } = useApp()
  const s = state.settings
  const [pinOpen, setPinOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const toggle = (k: keyof S, label: string) => (
    <label className="check between">
      <span>{label}</span>
      <input type="checkbox" checked={!!s[k]} onChange={(e) => setSettings({ [k]: e.target.checked })} />
    </label>
  )

  const run = async (action: () => Promise<void>, done?: string) => {
    setBusy(true)
    try {
      await action()
      if (done) notify(done)
    } catch (err) {
      notify((err as Error).message || 'No se pudo completar la acción.')
    }
    setBusy(false)
  }

  return (
    <>
      <h2>Visualización</h2>
      <section className="card form">
        <div className="between">
          <span>Moneda</span>
          <Seg label="Moneda de visualización" value={s.displayCurrency} onChange={(v) => setSettings({ displayCurrency: v })} options={[['CLP', 'CLP'], ['USD', 'USD']]} />
        </div>
        <div className="between">
          <span>Tema</span>
          <Seg label="Tema" value={s.theme} onChange={(v) => setSettings({ theme: v })} options={[['auto', 'Auto'], ['claro', 'Claro'], ['oscuro', 'Oscuro']]} />
        </div>
        <Field label="Actualizar cotizaciones en horario de mercado">
          <select value={s.autoRefreshMin} onChange={(e) => setSettings({ autoRefreshMin: Number(e.target.value) })}>
            <option value={0}>Solo manual</option>
            <option value={1}>Cada 1 minuto</option>
            <option value={5}>Cada 5 minutos</option>
            <option value={15}>Cada 15 minutos</option>
          </select>
        </Field>
        <p className="muted small">Aún no hay cotizaciones automáticas: la actualización periódica solo actúa sobre los datos de ejemplo.</p>
      </section>

      <h2>Notificaciones</h2>
      <section className="card form">
        {toggle('notifyPush', 'Notificaciones push')}
        {toggle('notifyEmail', 'Correo')}
        {toggle('notifyTelegram', 'Telegram')}
        <p className="muted small">Los canales aún no envían mensajes reales.</p>
      </section>

      <h2>Seguridad</h2>
      <section className="card form">
        <Field label="Pedir el PIN al volver a la app">
          <select value={s.autoLockMin} onChange={(e) => setSettings({ autoLockMin: Number(e.target.value) })}>
            <option value={0}>Siempre</option>
            <option value={1}>Tras 1 minuto fuera</option>
            <option value={5}>Tras 5 minutos fuera</option>
            <option value={-1}>Solo al abrirla de nuevo</option>
          </select>
        </Field>
        <label className="check between">
          <span>
            Desbloquear con huella o rostro
            {!security.bioSupported && <div className="muted small">No disponible en este navegador.</div>}
          </span>
          <input
            type="checkbox"
            checked={security.bioEnabled}
            disabled={busy || !security.bioSupported}
            onChange={(e) => run(e.target.checked ? security.enableBio : security.disableBio, e.target.checked ? 'Biometría activada.' : 'Biometría desactivada.')}
          />
        </label>
        <button className="btn" onClick={() => setPinOpen(!pinOpen)} aria-expanded={pinOpen}>Cambiar PIN</button>
        {pinOpen && <ChangePin onDone={() => (setPinOpen(false), notify('PIN cambiado.'))} />}
        <button className="btn" onClick={security.lock}>Bloquear ahora</button>
        <p className="muted small">Los datos están cifrados en este dispositivo. La app nunca pide ni guarda contraseñas de brokers.</p>
      </section>

      <h2>Datos</h2>
      <section className="card form">
        <button className="btn" disabled={busy} onClick={() => run(security.backup)}>Descargar respaldo cifrado</button>
        <label className="btn file-btn">
          Restaurar un respaldo
          <input
            type="file"
            accept=".json,application/json"
            hidden
            onChange={async (e) => {
              const f = e.target.files?.[0]
              e.target.value = ''
              if (!f || !confirm('El respaldo reemplazará los datos actuales y te pedirá el PIN que tenía ese respaldo. ¿Continuar?')) return
              const text = await f.text()
              run(() => security.restore(text))
            }}
          />
        </label>
        <p className="muted small">El respaldo se abre con el PIN que tenías al crearlo. Guárdalo fuera del celular.</p>
        <button className="btn" onClick={() => go('activo')}>Agregar un activo</button>
        <button className="btn" onClick={() => go('historial')}>Exportar operaciones (CSV)</button>
        <button className="btn" onClick={() => go('importar')}>Importar archivo de broker</button>
        {state.demo && (
          <button
            className="btn danger"
            onClick={() => confirm('¿Quitar los datos de ejemplo y dejar el portafolio vacío?') && replaceAll({ ...emptyState(), settings: s })}
          >
            Quitar datos de ejemplo
          </button>
        )}
        <button
          className="btn danger"
          onClick={() => confirm('Se borrarán TODOS los datos y el PIN de este dispositivo. No se puede deshacer. ¿Continuar?') && run(security.wipe)}
        >
          Borrar todo
        </button>
      </section>
      <p className="foot muted">Inversiones 0.2 · datos cifrados, guardados solo en este dispositivo{state.demo ? ' · usando datos de ejemplo' : ''}.</p>
    </>
  )
}

function ChangePin({ onDone }: { onDone: () => void }) {
  const { security } = useApp()
  const [oldPin, setOld] = useState('')
  const [pin, setPin] = useState('')
  const [pin2, setPin2] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const ok = !!oldPin && PIN_RULE.test(pin) && pin === pin2

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!ok) return
    setBusy(true)
    setError('')
    try {
      await security.changePin(oldPin, pin)
      onDone()
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }
  return (
    <form className="form" onSubmit={submit}>
      <PinInput label="PIN actual" value={oldPin} onChange={setOld} />
      <PinInput label={`PIN nuevo. ${PIN_HINT}`} value={pin} onChange={setPin} />
      <PinInput label="Repite el PIN nuevo" value={pin2} onChange={setPin2} />
      {error && <p className="loss small">{error}</p>}
      <button className="btn primary" disabled={!ok || busy}>{busy ? 'Guardando…' : 'Guardar PIN nuevo'}</button>
      <p className="muted small">Los respaldos anteriores siguen abriéndose con el PIN antiguo.</p>
    </form>
  )
}
