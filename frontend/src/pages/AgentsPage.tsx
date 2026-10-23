import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { Empty, GateSquares, Sparkline } from '../components/ui'
import { api, type Agent, type FeedItem } from '../lib/api'
import { ago, pct } from '../lib/format'

const KIND = { builtin: 'Built in Aether', http: 'Connected agent', openai_chat: 'OpenAI-compatible endpoint' } as const

function feedTime(iso: string) {
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  const today = new Date()
  if (d.toDateString() === today.toDateString()) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (today.getTime() - d.getTime() < 6 * 86400000) return d.toLocaleDateString([], { weekday: 'short' })
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

export function AgentsPage() {
  const nav = useNavigate()
  const agents = useQuery({ queryKey: ['agents'], queryFn: () => api.get<Agent[]>('/api/v1/agents'), refetchInterval: 15000 })
  const feed = useQuery({ queryKey: ['feed'], queryFn: () => api.get<FeedItem[]>('/api/v1/feed'), refetchInterval: 30000 })
  const list = agents.data ?? []
  const ready = list.filter((a) => a.latest_run?.gate?.passed).length
  const regressed = (feed.data ?? []).find((f) => f.kind === 'regression')

  return (
    <div className="page">
      <div className="pagehead">
        <div>
          <h1>Agents</h1>
          <p>
            {list.length === 0
              ? 'Bring an agent to Aether to see how reliably it calls tools, follows its rules and recovers from failures.'
              : `${list.length} agent${list.length === 1 ? '' : 's'} in this workspace. ${ready} cleared for production.${regressed ? ' ' + regressed.text : ''}`}
          </p>
        </div>
        <div className="actions">
          <Link className="btn" to="/agents/new?kind=external">Connect an agent you already run</Link>
          <Link className="btn ink" to="/agents/new">Build an agent in Aether</Link>
        </div>
      </div>

      <div className="split" style={{ marginTop: 36 }}>
        <div>
          {agents.isLoading && <div className="loading">Loading agents…</div>}
          {agents.error && <div className="error-box">{(agents.error as Error).message}</div>}
          {!agents.isLoading && list.length === 0 && (
            <Empty title="No agents yet" action={<Link className="btn ink" to="/agents/new">Build your first agent</Link>}>
              Define a prompt and tools here, or point Aether at an agent you already run. Aether writes the tests for you.
            </Empty>
          )}
          {list.length > 0 && (
            <>
              <div className="ahead"><span>Agent</span><span>Runs on</span><span>Release gate</span><span>Passed every attempt</span><span>Last run</span></div>
              <div className="alist">
                {list.map((a) => {
                  const lr = a.latest_run
                  return (
                    <div className="agent" key={a.id} onClick={() => nav(`/agents/${a.id}`)} role="link" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && nav(`/agents/${a.id}`)}>
                      <div>
                        <div className="nm">{a.name}</div>
                        <div className="sub">{a.description || 'No description'}</div>
                      </div>
                      <div className="src">
                        {a.kind === 'builtin' ? (lr?.model_name ?? 'Not run yet') : a.endpoint.url?.replace(/^https?:\/\//, '').slice(0, 28)}
                        {a.kind === 'builtin' && a.latest_version ? `, prompt v${a.latest_version.version}` : ''}
                        <br />
                        <span className="muted">{KIND[a.kind]}</span>
                      </div>
                      <div>{lr?.gate ? <GateSquares rules={lr.gate.rules} /> : <span className="small muted">No runs yet</span>}</div>
                      <div className="rel">
                        <span className="fig">{pct(lr?.metrics?.all_pass_rate)}</span>
                        <Sparkline values={(a.trend ?? []).map((t) => t.all_pass_rate)} />
                      </div>
                      <div className="src">{lr ? ago(lr.finished_at) : a.last_activity ? a.last_activity.status : '–'}</div>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>
        <aside className="feed">
          <h2>Since you were last here</h2>
          {(feed.data ?? []).length === 0 && <p className="small muted" style={{ paddingTop: 14 }}>Run an evaluation and its results will show up here.</p>}
          {(feed.data ?? []).map((f, i) => (
            <div className="item" key={i}>
              <time>{feedTime(f.at)}</time>
              <div>
                <p>{f.text}</p>
                <div className="act">
                  {f.run_id && <Link className="link" to={`/runs/${f.run_id}`}>Open run</Link>}
                  {f.kind === 'review' && <Link className="link" to="/review">Review verdicts</Link>}
                  {f.kind === 'untested_model' && <Link className="link" to="/runs/new">Add to a run</Link>}
                  {f.kind === 'stale_suite' && f.agent_id && <Link className="link" to={`/agents/${f.agent_id}/suites/new`}>Generate a fresh suite</Link>}
                </div>
              </div>
            </div>
          ))}
        </aside>
      </div>
    </div>
  )
}
