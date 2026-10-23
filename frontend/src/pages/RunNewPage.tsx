import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, type Agent, type Model, type Run, type Suite } from '../lib/api'
import { money, num } from '../lib/format'

export function RunNewPage() {
  const [params] = useSearchParams()
  const nav = useNavigate()
  const agents = useQuery({ queryKey: ['agents'], queryFn: () => api.get<Agent[]>('/api/v1/agents') })
  const [agentId, setAgentId] = useState(params.get('agent') ?? '')
  const agent = useQuery({ queryKey: ['agent', agentId], queryFn: () => api.get<Agent>(`/api/v1/agents/${agentId}`), enabled: !!agentId })
  const suites = useQuery({ queryKey: ['suites', agentId], queryFn: () => api.get<Suite[]>(`/api/v1/suites?agent_id=${agentId}`), enabled: !!agentId })
  const models = useQuery({ queryKey: ['models'], queryFn: () => api.get<Model[]>('/api/v1/models') })
  const [suiteId, setSuiteId] = useState(params.get('suite') ?? '')
  const [modelIds, setModelIds] = useState<string[]>([])
  const [versionIds, setVersionIds] = useState<string[]>([])
  const [trials, setTrials] = useState(3)
  const [toolMode, setToolMode] = useState<'simulated' | 'real'>('simulated')
  const [failure, setFailure] = useState(10)
  const [judge, setJudge] = useState('')
  const [sim, setSim] = useState('')
  const [name, setName] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const a = agent.data
  const ready = (suites.data ?? []).filter((s) => s.status === 'ready')
  useEffect(() => {
    if (!agentId && agents.data?.length) setAgentId(agents.data[0].id)
  }, [agents.data, agentId])
  useEffect(() => {
    if (ready.length && !ready.some((s) => s.id === suiteId)) setSuiteId(ready[0].id)
  }, [ready, suiteId])
  useEffect(() => {
    if (a?.versions?.length) {
      const latest = a.versions[a.versions.length - 1]
      setVersionIds([latest.id])
      setModelIds(latest.default_model_id ? [latest.default_model_id] : [])
    }
  }, [a?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const suite = ready.find((s) => s.id === suiteId)
  const tool = (models.data ?? []).filter((m) => m.enabled && m.supports_tools)
  const builtin = a?.kind === 'builtin'
  const variants = (builtin ? modelIds.length : 1) * Math.max(1, versionIds.length)
  const attempts = (suite?.task_count ?? 0) * variants * trials
  const est = builtin
    ? modelIds.reduce((s, id) => {
        const m = models.data?.find((x) => x.id === id)
        return s + ((m?.input_cost_per_mtok ?? 0) * 6000 + (m?.output_cost_per_mtok ?? 0) * 600) / 1e6
      }, 0) * (suite?.task_count ?? 0) * trials * Math.max(1, versionIds.length)
    : null
  const hasRealTools = a?.versions?.some((v) => v.tools.some((t) => t.http?.url))
  const toggle = (list: string[], set: (v: string[]) => void, id: string) => set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id])

  const start = useMutation({
    mutationFn: () => api.post<Run>('/api/v1/runs', {
      agent_id: agentId, suite_id: suiteId, name: name || null, model_ids: builtin ? modelIds : [], version_ids: versionIds, trials,
      tool_mode: toolMode, failure_rate: failure / 100, judge_model_id: judge || null, simulator_model_id: sim || null,
    }),
    onSuccess: (r) => nav(`/runs/${r.id}`),
    onError: (e: Error) => setErr(e.message),
  })

  return (
    <div className="page">
      <div className="crumb"><Link to="/runs">Runs</Link><span className="sep">/</span><span>New run</span></div>
      <div className="pagehead" style={{ marginTop: 14 }}>
        <div><h1>Run an evaluation</h1><p>Every task is attempted several times by every variant, so you see reliability rather than a lucky demo.</p></div>
      </div>
      <div className="split" style={{ marginTop: 30 }}>
        <div className="form" style={{ maxWidth: 'none' }}>
          <div className="cols">
            <label className="field">Agent
              <select className="select" value={agentId} onChange={(e) => { setAgentId(e.target.value); setSuiteId('') }}>
                {(agents.data ?? []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
              </select>
            </label>
            <label className="field">Test suite
              <select className="select" value={suiteId} onChange={(e) => setSuiteId(e.target.value)}>
                {ready.length === 0 && <option value="">No ready suites</option>}
                {ready.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.task_count} tasks)</option>)}
              </select>
            </label>
          </div>
          {a && ready.length === 0 && <div className="note-box">This agent has no ready test suite. <Link className="link" to={`/agents/${a.id}/suites/new`}>Generate one first.</Link></div>}
          {builtin && (
            <div>
              <div className="sec-h" style={{ marginBottom: 10 }}><h2>Models</h2><p>Pick several to compare them on the same tasks.</p></div>
              <div className="choice">
                {tool.map((m) => (
                  <label key={m.id} className={`opt ${modelIds.includes(m.id) ? 'on' : ''}`} style={{ cursor: 'pointer' }}>
                    <input type="checkbox" hidden checked={modelIds.includes(m.id)} onChange={() => toggle(modelIds, setModelIds, m.id)} />
                    <b>{m.display_name}</b>
                    <span>{m.provider_name}{m.input_cost_per_mtok ? `, $${m.input_cost_per_mtok}/$${m.output_cost_per_mtok} per 1M tokens` : ''}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          {(a?.versions?.length ?? 0) > 1 && (
            <div>
              <div className="sec-h" style={{ marginBottom: 10 }}><h2>Prompt versions</h2><p>Compare prompt changes on identical tasks.</p></div>
              <div className="choice">
                {a!.versions!.map((v) => (
                  <label key={v.id} className={`opt ${versionIds.includes(v.id) ? 'on' : ''}`} style={{ cursor: 'pointer' }}>
                    <input type="checkbox" hidden checked={versionIds.includes(v.id)} onChange={() => toggle(versionIds, setVersionIds, v.id)} />
                    <b>v{v.version}</b><span>{v.label ?? 'no label'}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <div>
            <div className="sec-h" style={{ marginBottom: 10 }}><h2>Attempts per task</h2><p>3 or more reveals flaky behaviour; 8 is what reliability benchmarks use.</p></div>
            <div className="seg">{[1, 2, 3, 5, 8].map((n) => <button key={n} className={trials === n ? 'on' : ''} onClick={() => setTrials(n)}>{n}</button>)}</div>
          </div>
          {a?.kind !== 'http' && (
            <div>
              <div className="sec-h" style={{ marginBottom: 10 }}><h2>When the agent calls a tool</h2></div>
              <div className="choice">
                <button className={`opt ${toolMode === 'simulated' ? 'on' : ''}`} onClick={() => setToolMode('simulated')}><b>Simulate the response</b><span>An LLM answers as your API would, from its schema and the task's fixtures. Safe to run anywhere.</span></button>
                <button className={`opt ${toolMode === 'real' ? 'on' : ''}`} onClick={() => setToolMode('real')} disabled={!hasRealTools}><b>Call your real APIs</b><span>{hasRealTools ? 'Requests go to the URLs on each tool. Aether sends X-Aether-Trial-Id so your sandbox can isolate state.' : 'None of this agent\'s tools have a real API URL.'}</span></button>
              </div>
              <label className="field" style={{ marginTop: 14 }}>Inject tool failures into {failure}% of calls<span className="hint">Timeouts, 5xx errors and malformed responses, to test whether the agent retries or explains honestly.</span>
                <input type="range" min={0} max={40} step={5} value={failure} onChange={(e) => setFailure(Number(e.target.value))} style={{ accentColor: 'var(--ink)' }} />
              </label>
            </div>
          )}
          <div className="cols">
            <label className="field">Judge model<span className="hint">Scores policy, grounding and assertions.</span>
              <select className="select" value={judge} onChange={(e) => setJudge(e.target.value)}>
                <option value="">Workspace default</option>
                {(models.data ?? []).filter((m) => m.enabled).map((m) => <option key={m.id} value={m.id}>{m.display_name} ({m.provider_name})</option>)}
              </select>
            </label>
            <label className="field">Simulator model<span className="hint">Plays your APIs and the user in conversations.</span>
              <select className="select" value={sim} onChange={(e) => setSim(e.target.value)}>
                <option value="">Workspace default</option>
                {(models.data ?? []).filter((m) => m.enabled).map((m) => <option key={m.id} value={m.id}>{m.display_name} ({m.provider_name})</option>)}
              </select>
            </label>
          </div>
          <label className="field">Run name<input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={a ? `${a.name} run` : ''} /></label>
          {err && <div className="error-box">{err}</div>}
        </div>
        <aside>
          <div className="sec-h"><h2>This run</h2></div>
          <dl className="kv" style={{ marginTop: 14 }}>
            <dt>Tasks</dt><dd className="fig">{num(suite?.task_count ?? 0)}</dd>
            <dt>Variants</dt><dd className="fig">{variants}</dd>
            <dt>Attempts</dt><dd className="fig">{num(attempts)}</dd>
            {est !== null && <><dt>Agent cost, estimate</dt><dd className="fig">{money(est)}</dd></>}
          </dl>
          <p className="small muted" style={{ marginTop: 10 }}>Judge and simulator calls add evaluation cost on top. Provider rate limits are respected automatically.</p>
          <button className="btn ink" style={{ marginTop: 18, width: '100%', justifyContent: 'center' }} disabled={!suiteId || attempts === 0 || start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? 'Starting…' : `Start ${num(attempts)} attempts`}
          </button>
        </aside>
      </div>
    </div>
  )
}
