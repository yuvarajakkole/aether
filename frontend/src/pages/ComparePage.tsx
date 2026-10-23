import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type Compare, type Run } from '../lib/api'
import { catLabel, money, pct, secs } from '../lib/format'

const LABELS: Record<string, [string, (v: number) => string, boolean]> = {
  pass_rate: ['Attempts passed', (v) => pct(v, 1), true],
  all_pass_rate: ['Passed every attempt', (v) => pct(v, 1), true],
  tool_selection: ['Right tools', (v) => pct(v, 1), true],
  argument_accuracy: ['Right arguments', (v) => pct(v, 1), true],
  recovery_rate: ['Recovered from errors', (v) => pct(v, 1), true],
  latency_p95_ms: ['p95 latency', (v) => secs(v), false],
  cost_per_trial_usd: ['Cost per attempt', (v) => money(v), false],
}

export function ComparePage() {
  const [p] = useSearchParams()
  const base = p.get('base')!, head = p.get('head')!
  const ra = useQuery({ queryKey: ['run', base], queryFn: () => api.get<Run>(`/api/v1/runs/${base}`) })
  const rb = useQuery({ queryKey: ['run', head], queryFn: () => api.get<Run>(`/api/v1/runs/${head}`) })
  const [bv, setBv] = useState<string>('')
  const [hv, setHv] = useState<string>('')
  const cmp = useQuery({
    queryKey: ['compare', base, head, bv, hv],
    queryFn: () => api.get<Compare>(`/api/v1/runs/${base}/compare/${head}?${new URLSearchParams({ ...(bv ? { base_variant: bv } : {}), ...(hv ? { head_variant: hv } : {}) })}`),
  })
  const vname = (r: Run | undefined, k: string) => {
    const v = r?.summary?.variants[k]
    return v ? `${v.model_name}${v.version ? `, prompt v${v.version}` : ''}` : k
  }
  const c = cmp.data
  return (
    <div className="page">
      <div className="crumb"><Link to="/runs">Runs</Link><span className="sep">/</span><span>Compare</span></div>
      <div className="pagehead" style={{ marginTop: 14 }}>
        <div>
          <h1>{base === head ? `Variants of run ${ra.data?.number ?? ''}` : `Run ${ra.data?.number ?? ''} against run ${rb.data?.number ?? ''}`}</h1>
          <p>Same tasks, compared attempt by attempt. A regression is a task that passed more often before.</p>
        </div>
      </div>
      <div className="cols" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginTop: 24, maxWidth: 760 }}>
        <label className="field">Before (run {ra.data?.number})<select className="select" value={bv || c?.base.variant || ''} onChange={(e) => setBv(e.target.value)}>{Object.keys(ra.data?.summary?.variants ?? {}).map((k) => <option key={k} value={k}>{vname(ra.data, k)}</option>)}</select></label>
        <label className="field">After (run {rb.data?.number})<select className="select" value={hv || c?.head.variant || ''} onChange={(e) => setHv(e.target.value)}>{Object.keys(rb.data?.summary?.variants ?? {}).map((k) => <option key={k} value={k}>{vname(rb.data, k)}</option>)}</select></label>
      </div>
      {cmp.error && <div className="error-box" style={{ marginTop: 20 }}>{(cmp.error as Error).message}</div>}
      {c && (
        <>
          <div className="deltas" style={{ marginTop: 30 }}>
            {Object.entries(LABELS).map(([k, [lab, fmt, higherBetter]]) => {
              const d = c.deltas[k]
              const after = c.head.metrics[k as keyof typeof c.head.metrics] as number | null | undefined
              const good = d === null || d === 0 ? null : higherBetter ? d > 0 : d < 0
              return (
                <div key={k}>
                  <span className="small muted">{lab}</span>
                  <span className="fig">{after === null || after === undefined ? '–' : fmt(after)}</span>
                  <span className={`small ${good === null ? 'muted' : good ? 'up' : 'down'}`}>{d === null ? 'n/a' : `${d > 0 ? '+' : ''}${k.includes('latency') ? secs(d) : k.includes('cost') ? money(d) : pct(d, 1)}`}</span>
                </div>
              )
            })}
          </div>
          <div className="sec two">
            <div>
              <div className="sec-h"><h2>Regressions</h2><p>{c.regressions.length} tasks got worse</p></div>
              {c.regressions.length === 0 && <p className="ink2" style={{ marginTop: 14 }}>No task got worse.</p>}
              {c.regressions.map((t) => (
                <div className="gt" key={t.task_id} style={{ gridTemplateColumns: '1fr auto' }}>
                  <div><div className="q">{t.input}</div><div className="e">{catLabel(t.category)}</div></div>
                  <span className="fig down" style={{ fontSize: 16, fontWeight: 600 }}>{t.base[0]}/{t.base[1]} → {t.head[0]}/{t.head[1]}</span>
                </div>
              ))}
            </div>
            <div>
              <div className="sec-h"><h2>Improvements</h2><p>{c.improvements.length} better, {c.unchanged} unchanged</p></div>
              {c.improvements.map((t) => (
                <div className="gt" key={t.task_id} style={{ gridTemplateColumns: '1fr auto' }}>
                  <div><div className="q">{t.input}</div><div className="e">{catLabel(t.category)}</div></div>
                  <span className="fig up" style={{ fontSize: 16, fontWeight: 600 }}>{t.base[0]}/{t.base[1]} → {t.head[0]}/{t.head[1]}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
