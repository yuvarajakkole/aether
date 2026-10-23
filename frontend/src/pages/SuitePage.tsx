import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Dialog, useToast } from '../components/ui'
import { api, type Agent, type Suite, type Task } from '../lib/api'
import { catLabel, money } from '../lib/format'

function Expect({ t }: { t: Task }) {
  const e = t.expected ?? {}
  const parts: React.ReactNode[] = []
  for (const c of e.tool_calls ?? []) {
    const args = Object.entries(c.arguments ?? {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ')
    parts.push(<span key={`c${c.name}${args}`}>calls <code>{c.name}{args ? `(${args})` : ''}</code></span>)
  }
  if (e.forbidden_tools?.length) parts.push(<span key="f">never calls <code>{e.forbidden_tools.join(', ')}</code></span>)
  if (e.output_contains?.length) parts.push(<span key="o">says “{e.output_contains.join('”, “')}”</span>)
  if (t.inject?.tool) parts.push(<span key="i"><code>{t.inject.tool}</code> will fail ({t.inject.kind})</span>)
  return (
    <div className="e">
      {parts.length > 0 && <div>Expects: {parts.reduce<React.ReactNode[]>((acc, p, i) => (i ? [...acc, '; ', p] : [p]), [])}.</div>}
      {(e.assertions ?? []).map((a, i) => <div key={i}>Judge checks: {a}</div>)}
      {t.scenario && <div>Simulated user, up to {t.max_turns} turns: {t.scenario}</div>}
    </div>
  )
}

function TaskDialog({ suite, task, onClose }: { suite: Suite; task: Task | null; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const init = task ? { category: task.category, input: task.input, scenario: task.scenario, max_turns: task.max_turns, expected: task.expected, fixtures: task.fixtures, inject: task.inject, tags: task.tags } : { category: 'everyday', input: '', expected: { tool_calls: [], assertions: [] }, fixtures: {} }
  const [text, setText] = useState(JSON.stringify(init, null, 2))
  const [err, setErr] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (body: unknown) => (task ? api.patch(`/api/v1/suites/${suite.id}/tasks/${task.id}`, body) : api.post(`/api/v1/suites/${suite.id}/tasks`, body)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['suite', suite.id] }); toast(task ? 'Task updated.' : 'Task added.'); onClose() },
    onError: (e: Error) => setErr(e.message),
  })
  const del = useMutation({
    mutationFn: () => api.del(`/api/v1/suites/${suite.id}/tasks/${task!.id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['suite', suite.id] }); toast('Task deleted.'); onClose() },
  })
  return (
    <Dialog title={task ? `Edit ${task.ref}` : 'Add a task'} onClose={onClose} footer={<>{task && <button className="btn ghost danger" onClick={() => del.mutate()}>Delete</button>}<span className="spacer" /><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn ink" onClick={() => { try { save.mutate(JSON.parse(text)) } catch (e) { setErr(`Invalid JSON: ${(e as Error).message}`) } }}>Save</button></>}>
      <textarea className="textarea code" style={{ minHeight: 360 }} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      {err && <div className="error-box" style={{ marginTop: 10 }}>{err}</div>}
    </Dialog>
  )
}

export function SuitePage() {
  const { id } = useParams()
  const nav = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const suite = useQuery({ queryKey: ['suite', id], queryFn: () => api.get<Suite>(`/api/v1/suites/${id}`), refetchInterval: (q) => (q.state.data?.status === 'generating' ? 2500 : false) })
  const s = suite.data
  const agent = useQuery({ queryKey: ['agent', s?.agent_id], queryFn: () => api.get<Agent>(`/api/v1/agents/${s!.agent_id}`), enabled: !!s })
  const [cat, setCat] = useState<string | null>(null)
  const [edit, setEdit] = useState<Task | null | 'new'>(null)
  const remove = useMutation({
    mutationFn: () => api.del(`/api/v1/suites/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['suites'] }); toast('Suite deleted.'); nav(`/agents/${s?.agent_id}`) },
  })
  if (suite.isLoading) return <div className="page loading">Loading suite…</div>
  if (!s) return <div className="page"><div className="error-box">{(suite.error as Error)?.message}</div></div>
  const tasks = (s.tasks ?? []).filter((t) => !cat || t.category === cat)
  const exportJson = async () => {
    const data = await api.get(`/api/v1/suites/${s.id}/export`)
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${s.name.replace(/\W+/g, '-').toLowerCase()}.json`
    a.click()
  }
  return (
    <div className="page">
      <div className="crumb"><Link to={`/agents/${s.agent_id}`}>{agent.data?.name ?? 'Agent'}</Link><span className="sep">/</span><span>{s.name}</span></div>
      <div className="pagehead" style={{ marginTop: 14 }}>
        <div>
          <h1>{s.name}</h1>
          <p>
            {s.status === 'generating'
              ? `Writing ${s.generation.requested ?? ''} tasks with ${s.generation.model ?? 'the generator model'}… this takes a minute or two.`
              : s.status === 'failed'
                ? `Generation failed: ${s.error}`
                : `${s.task_count} tasks${s.generation.generated ? `, written by ${s.generation.model} for ${money(s.generation.cost_usd)}` : ''}.`}
          </p>
          {!!s.generation.warnings?.length && <p className="small muted">{s.generation.warnings.join('; ')}</p>}
        </div>
        <div className="actions">
          <button className="btn ghost danger" onClick={() => confirm('Delete this suite and its runs?') && remove.mutate()}>Delete</button>
          <button className="btn" onClick={exportJson} disabled={!s.task_count}>Export JSON</button>
          <button className="btn" onClick={() => setEdit('new')}>Add task</button>
          <Link className="btn ink" to={`/runs/new?agent=${s.agent_id}&suite=${s.id}`} aria-disabled={!s.task_count} onClick={(e) => !s.task_count && e.preventDefault()}>Run this suite</Link>
        </div>
      </div>
      {s.status === 'generating' && <div className="progress" style={{ marginTop: 18 }}><i style={{ width: '40%', background: 'var(--pass-2)' }} /></div>}
      <div className="sec">
        <div className="sec-h">
          <h2>Tasks</h2>
          <div className="seg r">
            <button className={!cat ? 'on' : ''} onClick={() => setCat(null)}>All {s.task_count}</button>
            {Object.entries(s.categories ?? {}).map(([k, n]) => <button key={k} className={cat === k ? 'on' : ''} onClick={() => setCat(k)}>{catLabel(k)} {n}</button>)}
          </div>
        </div>
        {tasks.map((t) => (
          <div className="gt" key={t.id}>
            <span className="k">{t.ref}<br />{catLabel(t.category)}</span>
            <div><div className="q">{t.input}</div><Expect t={t} /></div>
            <button className="btn ghost sm" onClick={() => setEdit(t)}>Edit</button>
          </div>
        ))}
      </div>
      {edit && <TaskDialog suite={s} task={edit === 'new' ? null : edit} onClose={() => setEdit(null)} />}
    </div>
  )
}
