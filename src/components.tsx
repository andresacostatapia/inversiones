import { useEffect, useState, type ReactNode } from 'react'

/** Navegación por hash: funciona con el botón "atrás" del celular y sin servidor. */
export const go = (path: string) => {
  location.hash = '#/' + path
}
export function useRoute(): string[] {
  const read = () => location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent)
  const [parts, setParts] = useState(read)
  useEffect(() => {
    const on = () => {
      setParts(read())
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return parts
}

const paths: Record<string, ReactNode> = {
  home: <path d="M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />,
  pie: (
    <>
      <path d="M11 4a8.5 8.5 0 1 0 9 9h-9z" />
      <path d="M15 3.3A8.5 8.5 0 0 1 20.7 9H15z" />
    </>
  ),
  list: <path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01" />,
  bell: <path d="M6 9a6 6 0 1 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15 6 9zM10 20a2 2 0 0 0 4 0" />,
  sliders: (
    <>
      <path d="M4 6h8m5 0h3M4 12h2m5 0h9M4 18h10m5 0h1" />
      <circle cx="14.5" cy="6" r="2.2" />
      <circle cx="8.5" cy="12" r="2.2" />
      <circle cx="16.5" cy="18" r="2.2" />
    </>
  ),
  refresh: <path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" />,
  plus: <path d="M12 5v14M5 12h14" />,
  back: <path d="M15 5l-7 7 7 7" />,
}

export function Icon({ name, size = 22 }: { name: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  )
}

/** Variación con color Y símbolo, para que no dependa solo del color. */
export function Delta({ value, children }: { value: number; children: ReactNode }) {
  const cls = value > 0 ? 'gain' : value < 0 ? 'loss' : 'flat'
  return (
    <span className={`num ${cls}`}>
      <span className="arrow">{value > 0 ? '▲' : value < 0 ? '▼' : '•'}</span> {children}
    </span>
  )
}

export function Seg<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: [T, string][]
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={v} type="button" className={v === value ? 'on' : ''} aria-pressed={v === value} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  )
}

export function Spark({ data, up }: { data: number[]; up: boolean }) {
  const min = Math.min(...data)
  const max = Math.max(...data)
  const pts = data
    .map((v, i) => `${((i / (data.length - 1)) * 300).toFixed(1)},${(76 - ((v - min) / (max - min || 1)) * 72).toFixed(1)}`)
    .join(' ')
  return (
    <svg className={`spark ${up ? 'gain' : 'loss'}`} viewBox="0 0 300 80" preserveAspectRatio="none" role="img" aria-label="Evolución del precio, 90 días">
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

export const palette = ['#0f6f93', '#c98a1b', '#7566b3', '#3d8b6e', '#b05874', '#6c7a89', '#8a6d3b']

export function AllocationBar({ items }: { items: { label: string; pct: number }[] }) {
  return (
    <>
      <div className="alloc-bar" aria-hidden="true">
        {items.map((it, i) => (
          <span key={it.label} style={{ width: `${it.pct}%`, background: palette[i % palette.length] }} />
        ))}
      </div>
      <ul className="alloc-legend">
        {items.map((it, i) => (
          <li key={it.label}>
            <i style={{ background: palette[i % palette.length] }} />
            <span>{it.label}</span>
            <b className="num">{it.pct.toFixed(1).replace('.', ',')}%</b>
          </li>
        ))}
      </ul>
    </>
  )
}
