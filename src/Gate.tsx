import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { App } from './App'
import { download } from './lib/csv'
import { today } from './lib/format'
import {
  bioSupported, changePin, createVault, cryptoAvailable, deleteRecord, disableBio, enableBio, exportBackup,
  loadRecord, parseBackup, storeRecord, unlockWithBio, unlockWithPin, type Session, type VaultRecord,
} from './lib/vault'
import { AppProvider, demoState, emptyState, hydrate, type Security, type State } from './store'

type Phase =
  | { k: 'loading' }
  | { k: 'error'; msg: string }
  | { k: 'new' }
  | { k: 'locked'; rec: VaultRecord }
  | { k: 'open'; session: Session; data: State }

export const PIN_RULE = /^\d{6,12}$/
export const PIN_HINT = 'Entre 6 y 12 dígitos.'

/** Decide qué se muestra: crear PIN, pantalla de bloqueo o la app desbloqueada. */
export function Gate() {
  const [phase, setPhase] = useState<Phase>({ k: 'loading' })
  const [, bump] = useState(0)

  useEffect(() => {
    // Limpia los datos sin cifrar que dejaba el primer prototipo.
    try {
      localStorage.removeItem('inversiones-proto-v1')
    } catch {
      /* sin acceso a localStorage */
    }
    if (!cryptoAvailable()) {
      setPhase({ k: 'error', msg: 'Para cifrar tus datos la app debe abrirse por HTTPS (o en localhost) y en un navegador con almacenamiento habilitado.' })
      return
    }
    loadRecord().then(
      (rec) => setPhase(rec ? { k: 'locked', rec } : { k: 'new' }),
      () => setPhase({ k: 'error', msg: 'No se pudo abrir el almacenamiento del navegador. Si estás en modo privado, usa una ventana normal.' }),
    )
  }, [])

  const restore = async (text: string) => {
    const rec = parseBackup(text)
    await storeRecord(rec)
    setPhase({ k: 'locked', rec })
  }

  if (phase.k === 'loading') return <Auth title="Inversiones">{null}</Auth>
  if (phase.k === 'error') return <Auth title="Inversiones"><p className="loss">{phase.msg}</p></Auth>

  if (phase.k === 'new')
    return (
      <Setup
        onCreate={async (pin, demo) => {
          const data = demo ? demoState() : emptyState()
          setPhase({ k: 'open', session: await createVault(pin, data), data })
        }}
        onRestore={restore}
      />
    )

  if (phase.k === 'locked')
    return (
      <Lock
        rec={phase.rec}
        onOpen={({ session, data }) => setPhase({ k: 'open', session, data: hydrate(data) })}
        onRestore={restore}
        onWipe={async () => {
          await deleteRecord()
          setPhase({ k: 'new' })
        }}
      />
    )

  const { session } = phase
  const security: Security = {
    bioSupported: bioSupported(),
    bioEnabled: !!session.rec.bio,
    lock: () => void session.queue.finally(() => setPhase({ k: 'locked', rec: session.rec })),
    changePin: (oldPin, newPin) => changePin(session, oldPin, newPin),
    enableBio: async () => {
      await enableBio(session)
      bump((n) => n + 1)
    },
    disableBio: async () => {
      await disableBio(session)
      bump((n) => n + 1)
    },
    backup: async () => {
      await session.queue.catch(() => {})
      download(`inversiones-respaldo-${today()}.json`, exportBackup(session.rec), 'application/json')
    },
    restore: async (text) => {
      const rec = parseBackup(text)
      await session.queue.catch(() => {})
      await storeRecord(rec)
      setPhase({ k: 'locked', rec })
    },
    wipe: async () => {
      await session.queue.catch(() => {})
      await deleteRecord()
      setPhase({ k: 'new' })
    },
  }
  return (
    <AppProvider initial={phase.data} session={session} security={security}>
      <App />
    </AppProvider>
  )
}

function Auth({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="auth">
      <img src={`${import.meta.env.BASE_URL}icon.svg`} alt="" width="56" height="56" />
      <h1>{title}</h1>
      {children}
    </div>
  )
}

export function PinInput({ value, onChange, label, autoFocus }: { value: string; onChange: (v: string) => void; label: string; autoFocus?: boolean }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="password"
        inputMode="numeric"
        autoComplete="off"
        maxLength={12}
        autoFocus={autoFocus}
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, ''))}
      />
    </label>
  )
}

function RestoreButton({ onRestore, onError, confirmText }: { onRestore: (text: string) => Promise<void>; onError: (msg: string) => void; confirmText?: string }) {
  return (
    <label className="link file-link">
      Restaurar desde un respaldo
      <input
        type="file"
        accept=".json,application/json"
        hidden
        onChange={async (e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (!f || (confirmText && !confirm(confirmText))) return
          try {
            await onRestore(await f.text())
          } catch (err) {
            onError((err as Error).message)
          }
        }}
      />
    </label>
  )
}

function Setup({ onCreate, onRestore }: { onCreate: (pin: string, demo: boolean) => Promise<void>; onRestore: (text: string) => Promise<void> }) {
  const [pin, setPin] = useState('')
  const [pin2, setPin2] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const ok = PIN_RULE.test(pin) && pin === pin2

  const create = (demo: boolean) => async () => {
    setBusy(true)
    try {
      await onCreate(pin, demo)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  return (
    <Auth title="Crea tu PIN">
      <p className="muted">
        Tus datos se guardan cifrados solo en este dispositivo. El PIN es la llave: <b>si lo olvidas, no hay forma de recuperarlos</b> salvo
        con un respaldo cuyo PIN recuerdes.
      </p>
      <form className="card form" onSubmit={(e) => e.preventDefault()}>
        <PinInput label={`PIN nuevo. ${PIN_HINT}`} value={pin} onChange={setPin} autoFocus />
        <PinInput label="Repite el PIN" value={pin2} onChange={setPin2} />
        {pin2.length >= pin.length && pin2 !== '' && pin !== pin2 && <p className="loss small">Los PIN no coinciden.</p>}
        {error && <p className="loss small">{error}</p>}
        <button className="btn primary" disabled={!ok || busy} onClick={create(false)}>{busy ? 'Cifrando…' : 'Empezar con portafolio vacío'}</button>
        <button className="btn" disabled={!ok || busy} onClick={create(true)}>Empezar con datos de ejemplo</button>
      </form>
      <RestoreButton onRestore={onRestore} onError={setError} />
    </Auth>
  )
}

function Lock({ rec, onOpen, onRestore, onWipe }: {
  rec: VaultRecord
  onOpen: (r: { session: Session; data: unknown }) => void
  onRestore: (text: string) => Promise<void>
  onWipe: () => Promise<void>
}) {
  const [pin, setPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [fails, setFails] = useState(0)
  const [wait, setWait] = useState(0)
  const [forgot, setForgot] = useState(false)

  // Tras 3 intentos fallidos, cada nuevo intento exige esperar 30 segundos.
  useEffect(() => {
    if (wait <= 0) return
    const id = setTimeout(() => setWait(wait - 1), 1000)
    return () => clearTimeout(id)
  }, [wait])

  const attempt = async (run: () => Promise<{ session: Session; data: unknown }>, countFail: boolean) => {
    setBusy(true)
    setError('')
    try {
      onOpen(await run())
    } catch (err) {
      setError((err as Error).message || 'No se pudo desbloquear.')
      setPin('')
      if (countFail) {
        if (fails + 1 >= 3) setWait(30)
        setFails(fails + 1)
      }
      setBusy(false)
    }
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (pin && !busy && wait === 0) attempt(() => unlockWithPin(rec, pin), true)
  }

  return (
    <Auth title="Inversiones">
      <form className="card form" onSubmit={submit}>
        <PinInput label="PIN" value={pin} onChange={setPin} autoFocus />
        {error && <p className="loss small">{error}</p>}
        {wait > 0 && <p className="muted small">Demasiados intentos. Espera {wait} s.</p>}
        <button className="btn primary" disabled={!pin || busy || wait > 0}>{busy ? 'Abriendo…' : 'Desbloquear'}</button>
        {rec.bio && (
          <button type="button" className="btn" disabled={busy} onClick={() => attempt(() => unlockWithBio(rec), false)}>
            Usar huella o rostro
          </button>
        )}
      </form>
      <button className="link" onClick={() => setForgot(!forgot)} aria-expanded={forgot}>Olvidé mi PIN</button>
      {forgot && (
        <div className="card form">
          <p className="muted">
            Sin el PIN los datos de este dispositivo no se pueden leer. Puedes restaurar un respaldo (te pedirá el PIN que tenía ese
            respaldo) o borrar todo y empezar de nuevo.
          </p>
          <RestoreButton onRestore={onRestore} onError={setError} confirmText="El respaldo reemplazará los datos de este dispositivo. ¿Continuar?" />
          <button className="link danger" onClick={() => confirm('Se borrarán TODOS los datos de este dispositivo. No se puede deshacer. ¿Continuar?') && onWipe()}>
            Borrar todo y empezar de nuevo
          </button>
        </div>
      )}
    </Auth>
  )
}
