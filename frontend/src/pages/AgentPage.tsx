import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Dialog, Empty, RunStatus, Sparkline, useToast } from '../components/ui'
import { api, type Agent, type Gate, type Meta, type Model, type Run, type Suite } from '../lib/api'
import { ago, catLabel, pct } from '../lib/format'

function GateEditor({ agent, meta, onClose }: { agent: Agent; meta?: Meta; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [text, setText] = useState(JSON.stringify(agent.gate?.rules?.length ? agent.gate : meta?.default_gate ?? { name: 'production', rules: [] }, null, 2))
  const [err, setErr] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (gate: Gate) => api.patch<Agent>(`/api/v1/agents/${agent.id}`, { gate }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['agent', agent.id] }); toast('Release gate saved. It applies to new and re-analysed runs.'); onClose() },
    onError: (e: Error) => setErr(e.message),
  })
  return (
    <Dialog title="Release gate" onClose={onClose} footer={<><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn ink" onClick={() => { try { save.mutate(JSON.parse(text)) } catch (e) { setErr(`Invalid JSON: ${(e as Error).message}`) } }}>Save gate</button></>}>
      <p className="small ink2" style={{ marginBottom: 10 }}>Rules every variant must meet to be cleared for production. Metrics: {meta?.gate_metrics.join(', ')}.</p>
      <textarea className="textarea code" style={{ minHeight: 260 }} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      {err && <div className="error-box" style={{ marginTop: 10 }}>{err}</div>}
    </Dialog>
  )
}

function VersionDialog({ agent, onClose }: { agent: Agent; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const latest = agent.versions?.[agent.versions.length - 1]
  const [prompt, setPrompt] = useState(latest?.system_prompt ?? '')
  const [tools, setTools] = useState(JSON.stringify(latest?.tools ?? [], null, 2))
  const [labelText, setLabel] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (body: unknown) => api.post(`/api/v1/agents/${agent.id}/versions`, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['agent', agent.id] }); toast('New prompt version saved.'); onClose() },
    onError: (e: Error) => setErr(e.message),
  })
  const submit = () => {
    try {
      save.mutate({ label: labelText || null, system_prompt: prompt, tools: JSON.parse(tools), default_model_id: latest?.default_model_id, params: latest?.params ?? {} })
    } catch (e) {
      setErr(`Tools are not valid JSON: ${(e as Error).message}`)
    }
  }
  return (
    <Dialog title={`New version (v${(latest?.version ?? 0) + 1})`} onClose={onClose} footer={<><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn ink" disabled={save.isPending} onClick={submit}>Save version</button></>}>
      <div className="form">
        <label className="field">What changed<input className="input" value={labelText} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. stricter refund rule" /></label>
        <label className="field">System prompt<textarea className="textarea" style={{ minHeight: 200 }} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></label>
        <label className="field">Tools (JSON)<textarea className="textarea code" value={tools} onChange={(e) => setTools(e.target.value)} spellCheck={false} /></label>
        {err && <div className="error-box">{err}</div>}
      </div>
    </Dialog>
  )
}

export function AgentPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const toast = useToast()
  const qc = useQueryClient()
  const agent = useQuery({ queryKey: ['agent', id], queryFn: () => api.get<Agent>(`/api/v1/agents/${id}`) })
  const suites = useQuery({ queryKey: ['suites', id], queryFn: () => api.get<Suite[]>(`/api/v1/suites?agent_id=${id}`), refetchInterval: (q) => (q.state.data?.some((s) => s.status === 'generating') ? 3000 : false) })
  const runs = useQuery({ queryKey: ['runs', id], queryFn: () => api.get<Run[]>(`/api/v1/runs?agent_id=${id}`), refetchInterval: 8000 })
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.get<Meta>('/api/v1/meta'), staleTime: Infinity })
  const models = useQuery({ queryKey: ['models'], queryFn: () => api.get<Model[]>('/api/v1/models') })
  const [dialog, setDialog] = useState<'gate' | 'version' | null>(null)
  const [showVersion, setShowVersion] = useState<number | null>(null)
  const remove = useMutation({
    mutationFn: () => api.del(`/api/v1/agents/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['agents'] }); toast('Agent deleted.'); nav('/') },
    onError: (e: Error) => toast(e.message, true),
  })
  const a = agent.data
  if (agent.isLoading) return <div className="page loading">Loading agent…</div>
  if (!a) return <div className="page"><div className="error-box">{(agent.error as Error)?.message ?? 'Agent not found'}</div></div>
  const versions = a.versions ?? []
  const v = versions.find((x) => x.version === showVersion) ?? versions[versions.length - 1]
  const completed = (runs.data ?? []).filter((r) => r.status === 'completed').reverse()
  const trend = completed.map((r) => Math.max(...Object.values(r.variants ?? {}).map((x) => x.all_pass_rate ?? 0)))
  const ready = (suites.data ?? []).filter((s) => s.status === 'ready')
  const modelName = (mid: string | null) => models.data?.find((m) => m.id === mid)?.display_name

  return (
    <div className="page">
      <div className="crumb"><Link to="/">Agents</Link><span className="sep">/</span><span>{a.name}</span></div>
      <div className="pagehead" style={{ marginTop: 14 }}>
        <div>
          <h1>{a.name}</h1>
          <p>{a.description || 'No description.'}</p>
        </div>
        <div className="actions">
          <button className="btn ghost danger" onClick={() => confirm(`Delete ${a.name} and all its suites and runs?`) && remove.mutate()}>Delete</button>
          <button className="btn" onClick={() => setDialog('gate')}>Release gate</button>
          <Link className="btn" to={`/agents/${a.id}/suites/new`}>New test suite</Link>
          <Link className={`btn ink`} to={ready.length ? `/runs/new?agent=${a.id}` : `/agents/${a.id}/suites/new`}>{ready.length ? 'Run an evaluation' : 'Create tests first'}</Link>
        </div>
      </div>

      <div className="split" style={{ marginTop: 36 }}>
        <div>
          <div className="sec-h"><h2>Runs</h2><p>{completed.length} completed</p>{trend.length > 1 && <span className="r"><Sparkline values={trend} width={120} height={26} /></span>}</div>
          {(runs.data ?? []).length === 0 && <p className="ink2" style={{ marginTop: 16 }}>No runs yet.</p>}
          <table className="table">
            <tbody>
              {(runs.data ?? []).map((r) => (
                <tr key={r.id} className="click" onClick={() => nav(`/runs/${r.id}`)}>
                  <td style={{ width: 60 }} className="fig">{r.number}</td>
                  <td><div>{r.name}</div><div className="small muted">{r.headline ?? ''}</div></td>
                  <td><RunStatus status={r.status} /></td>
                  <td className="num fig">{pct(Math.max(0, ...Object.values(r.variants ?? {}).map((x) => x.all_pass_rate ?? 0)))}</td>
                  <td className="small muted" style={{ whiteSpace: 'nowrap' }}>{ago(r.created_at)}{a.baseline_run_id === r.id ? ', baseline' : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="sec">
            <div className="sec-h"><h2>Test suites</h2><p>{ready.length} ready</p></div>
            {(suites.data ?? []).length === 0 && (
              <Empty title="No tests yet" action={<Link className="btn ink" to={`/agents/${a.id}/suites/new`}>Generate a test suite</Link>}>
                Aether reads this agent's prompt and tools and writes tasks aimed at where it is most likely to break.
              </Empty>
            )}
            <table className="table">
              <tbody>
                {(suites.data ?? []).map((s) => (
                  <tr key={s.id} className="click" onClick={() => nav(`/suites/${s.id}`)}>
                    <td><div>{s.name}</div><div className="small muted">{Object.entries(s.categories ?? {}).map(([k, n]) => `${n} ${catLabel(k).toLowerCase()}`).join(', ') || s.error || ''}</div></td>
                    <td><span className={`status ${s.status === 'ready' ? '' : s.status === 'failed' ? 'bad' : 'live'}`}>{s.status === 'generating' ? 'Writing tasks…' : s.status}</span></td>
                    <td className="num fig">{s.task_count ?? 0} tasks</td>
                    <td className="small muted">{ago(s.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <aside>
          <div className="sec-h"><h2>{a.kind === 'builtin' ? 'Definition' : 'Connection'}</h2>{a.kind === 'builtin' && <span className="r"><button className="btn sm" onClick={() => setDialog('version')}>New version</button></span>}</div>
          <dl className="kv" style={{ marginTop: 14 }}>
            <dt>Type</dt><dd>{a.kind === 'builtin' ? 'Built in Aether' : a.kind === 'http' ? 'Aether agent protocol' : 'OpenAI-compatible endpoint'}</dd>
            {a.endpoint.url && <><dt>Endpoint</dt><dd className="code">{a.endpoint.url}</dd></>}
            {a.kind !== 'builtin' && <><dt>Credentials</dt><dd>{a.endpoint.has_api_key ? 'API key stored, encrypted' : 'None'}{a.endpoint.header_names?.length ? `; headers ${a.endpoint.header_names.join(', ')}` : ''}</dd></>}
            <dt>Owner</dt><dd>{a.owner ?? '–'}</dd>
            <dt>Gate</dt><dd>{a.gate?.rules?.length ? `${a.gate.name}, ${a.gate.rules.length} rules` : 'Workspace default'}</dd>
            {v && a.kind === 'builtin' && <><dt>Default model</dt><dd>{modelName(v.default_model_id) ?? 'Chosen per run'}</dd></>}
          </dl>
          {versions.length > 0 && (a.kind === 'builtin' || (v?.tools.length ?? 0) > 0) && (
            <>
              {versions.length > 1 && (
                <div className="seg" style={{ marginTop: 18 }}>
                  {versions.map((x) => <button key={x.id} className={x.id === v?.id ? 'on' : ''} onClick={() => setShowVersion(x.version)}>v{x.version}</button>)}
                </div>
              )}
              {v?.label && <p className="small muted" style={{ marginTop: 10 }}>v{v.version}: {v.label}</p>}
              {v?.system_prompt && <div className="prompt">{v.system_prompt}</div>}
              <div style={{ marginTop: 18 }}>
                <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{v?.tools.length ?? 0} tools</h3>
                {v?.tools.map((t) => (
                  <div className="sig" key={t.name}>
                    <b>{t.name}</b>({Object.keys((t.parameters as { properties?: object }).properties ?? {}).join(', ')})
                    {t.http?.url && <div className="small muted">real API: {t.http.method ?? 'POST'} {t.http.url}</div>}
                  </div>
                ))}
              </div>
            </>
          )}
        </aside>
      </div>
      {dialog === 'gate' && <GateEditor agent={a} meta={meta.data} onClose={() => setDialog(null)} />}
      {dialog === 'version' && <VersionDialog agent={a} onClose={() => setDialog(null)} />}
    </div>
  )
}
