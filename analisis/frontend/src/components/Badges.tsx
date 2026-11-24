import { pct, signClass } from '../format'
import type { Freshness, Label } from '../types'

const LABEL_STYLE: Record<Label, { cls: string; icon: string }> = {
  COMPRAR: { cls: 'buy', icon: '▲' },
  MANTENER: { cls: 'hold', icon: '●' },
  VENDER: { cls: 'sell', icon: '▼' },
}

export function SignalBadge({ label, big = false }: { label: Label | null | undefined; big?: boolean }) {
  if (!label) return <span className="badge hold" title="Sin datos suficientes">— sin señal</span>
  const s = LABEL_STYLE[label]
  return (
    <span className={`badge ${s.cls}${big ? ' big' : ''}`}>
      <span aria-hidden="true">{s.icon}</span>
      {label}
    </span>
  )
}

const FRESH_ICON: Record<Freshness['kind'], string> = {
  tiempo_real: '●',
  retraso: '◔',
  cierre: '◼',
  desactualizado: '⚠',
  diario: '◷',
  sin_datos: '⚠',
}

const SHORT: Record<Freshness['kind'], string> = {
  tiempo_real: 'Tiempo real',
  retraso: 'Retraso',
  cierre: 'Cerrado',
  desactualizado: 'Desactualizado',
  diario: 'Diario',
  sin_datos: 'Sin datos',
}

/** "11:15" si es de hoy; "20/11" si es de otro día. */
function shortTime(iso: string | null): string {
  if (!iso) return ''
  const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'America/Santiago' })
  return iso.slice(0, 10) === today ? iso.slice(11, 16) : `${iso.slice(8, 10)}/${iso.slice(5, 7)}`
}

/** Siempre visible: si el dato es en tiempo real o con retraso, y su hora. */
export function FreshnessBadge({ f, short = false }: { f: Freshness | undefined; short?: boolean }) {
  if (!f) return null
  const text = short ? `${SHORT[f.kind]} ${shortTime(f.as_of_local)}`.trim() : f.label
  return (
    <span className={`fresh ${f.kind}`} title={f.label}>
      <span className="ico" aria-hidden="true">{FRESH_ICON[f.kind]}</span>
      {text}
    </span>
  )
}

export function Change({ v }: { v: number | null | undefined }) {
  return <span className={`num ${signClass(v)}`}>{pct(v, 2)}</span>
}
