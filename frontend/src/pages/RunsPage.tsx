import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Empty, RunStatus } from '../components/ui'
import { api, type Agent, type Run } from '../lib/api'
import { ago, money, num, pct } from '../lib/format'

export function RunsPage() {
  const nav = useNavigate()
  const runs = useQuery({ queryKey: ['runs'], queryFn: () => api.get<Run[]>('/api/v1/runs?limit=100'), refetchInterval: 5000 })
  const agents = useQuery({ queryKey: ['agents'], queryFn: () => api.get<Agent[]>('/api/v1/agents') })
  const [pick, setPick] = useState<string[]>([])
  const name = (id: string) => agents.data?.find((a) => a.id === id)?.name ?? ''
  const list = runs.data ?? []
  const toggle = (id: string) => setPick((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]))
  const [a, b] = pick.map((id) => list.find((r) => r.id === id)).filter(Boolean) as Run[]
  const comparable = a && b && a.suite_id === b.suite_id
  return (
    <div className="page">
      <div className="pagehead">
        <div><h1>Runs</h1><p>Every evaluation in this workspace. Tick two runs of the same suite to compare them.</p></div>
        <div className="actions">
          {pick.length === 2 && (comparable
            ? <Link className="btn" to={`/compare?base=${[a, b].sort((x, y) => x.number - y.number)[0].id}&head=${[a, b].sort((x, y) => x.number - y.number)[1].id}`}>Compare runs {Math.min(a.number, b.number)} and {Math.max(a.number, b.number)}</Link>
            : <span className="small muted">Pick two runs of the same suite to compare</span>)}
          <Link className="btn ink" to="/runs/new">New run</Link>
        </div>
      </div>
      {runs.isLoading && <div className="loading">Loading runs…</div>}
      {!runs.isLoading && list.length === 0 && <Empty title="No runs yet" action={<Link className="btn ink" to="/runs/new">Run your first evaluation</Link>}>Pick an agent, a suite and the models to compare.</Empty>}
      {list.length > 0 && (
        <table className="table" style={{ marginTop: 30 }}>
          <thead><tr><th style={{ width: 28 }} /><th>Run</th><th>Agent</th><th>Status</th><th className="num">Attempts</th><th className="num">Best passed all</th><th className="num">Spend</th><th>Started</th></tr></thead>
          <tbody>
            {list.map((r) => {
              const best = Math.max(0, ...Object.values(r.variants ?? {}).map((v) => v.all_pass_rate ?? 0))
              return (
                <tr key={r.id} className="click" onClick={() => nav(`/runs/${r.id}`)}>
                  <td onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={pick.includes(r.id)} onChange={() => toggle(r.id)} aria-label={`Select run ${r.number}`} style={{ accentColor: 'var(--ink)' }} /></td>
                  <td><span className="fig" style={{ fontWeight: 600, marginRight: 10 }}>{r.number}</span>{r.name}<div className="small muted" style={{ maxWidth: '70ch' }}>{r.headline}</div></td>
                  <td>{name(r.agent_id)}</td>
                  <td><RunStatus status={r.status} /></td>
                  <td className="num fig">{num(r.totals?.trials ?? r.config.task_count * r.config.trials * r.config.model_ids.length * Math.max(1, r.config.version_ids.length))}</td>
                  <td className="num fig">{r.status === 'completed' ? pct(best) : '–'}</td>
                  <td className="num fig">{r.totals ? money(r.totals.cost_usd + r.totals.eval_cost_usd) : '–'}</td>
                  <td className="small muted">{ago(r.created_at)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
