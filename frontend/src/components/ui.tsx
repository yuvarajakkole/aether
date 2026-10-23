import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { passColor } from '../lib/format'

type Toast = { id: number; text: string; bad?: boolean }
const ToastCtx = createContext<(text: string, bad?: boolean) => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<Toast | null>(null)
  const show = useCallback((text: string, bad?: boolean) => setToast({ id: Date.now(), text, bad }), [])
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), toast.bad ? 6000 : 3000)
    return () => clearTimeout(t)
  }, [toast])
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {toast && (
        <div className={`toast ${toast.bad ? 'bad' : ''}`} role="status">
          {toast.text}
        </div>
      )}
    </ToastCtx.Provider>
  )
}
export const useToast = () => useContext(ToastCtx)

export function Logo() {
  return (
    <span className="logo" aria-hidden="true">
      {[0, 1, 2, 3, 4, 5, 6].map((i) => (
        <i key={i} />
      ))}
      <i className="y" />
    </span>
  )
}

export function Dialog({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    addEventListener('keydown', k)
    return () => removeEventListener('keydown', k)
  }, [onClose])
  return (
    <div className="dialog-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-label={title}>
        <div className="dialog-h">
          <h2>{title}</h2>
          <button className="btn ghost sm spacer" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="dialog-b">{children}</div>
        {footer && <div className="dialog-f">{footer}</div>}
      </div>
    </div>
  )
}

export function Sparkline({ values, width = 64, height = 22, color }: { values: (number | null)[]; width?: number; height?: number; color?: string }) {
  const v = values.filter((x): x is number => x !== null && x !== undefined)
  if (v.length < 2) return <svg className="spark" width={width} height={height} aria-hidden="true" />
  const pts = v.map((y, i) => `${(i / (v.length - 1)) * width},${height - 2 - y * (height - 4)}`).join(' ')
  const trend = v[v.length - 1] - v[0]
  const stroke = color ?? (trend < -0.04 ? 'var(--fail)' : trend > 0.02 ? 'var(--pass)' : 'var(--ink-3)')
  return (
    <svg className="spark" width={width} height={height} aria-label={`trend from ${Math.round(v[0] * 100)}% to ${Math.round(v[v.length - 1] * 100)}%`}>
      <polyline fill="none" stroke={stroke} strokeWidth="1.6" points={pts} />
    </svg>
  )
}

/** One bar per task, sorted from always solved to never solved. */
export function Strip({ counts, total }: { counts: number[]; total: number }) {
  const sorted = [...counts].sort((a, b) => b - a)
  return (
    <>
      <div className="strip" role="img" aria-label={`${sorted.filter((c) => c === total).length} of ${sorted.length} tasks solved on every attempt`}>
        {sorted.map((n, i) => (
          <i key={i} style={{ height: 2 + (n / Math.max(total, 1)) * 26, background: passColor(n, total) }} />
        ))}
      </div>
      <div className="scale">
        <span>always solved</span>
        <span>never solved</span>
      </div>
    </>
  )
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  )
}

export function GateSquares({ rules }: { rules: { passed?: boolean }[] }) {
  const passed = rules.filter((r) => r.passed).length
  return (
    <div className="gates" aria-label={`${passed} of ${rules.length} gate rules pass`}>
      {rules.map((r, i) => (
        <i key={i} className={r.passed ? '' : 'f'} />
      ))}
      <span>
        {passed} of {rules.length}
      </span>
    </div>
  )
}

export function RunStatus({ status }: { status: string }) {
  const cls = status === 'completed' ? '' : status === 'failed' ? 'bad' : status === 'cancelled' ? 'idle' : 'live'
  const label = { queued: 'Queued', running: 'Running', analyzing: 'Analysing results', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled' }[status] ?? status
  return <span className={`status ${cls}`}>{label}</span>
}
