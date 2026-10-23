import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useToast } from '../components/ui'
import { api, type Agent, type Meta, type Model, type Suite } from '../lib/api'
import { catLabel } from '../lib/format'

const COLORS: Record<string, string> = { everyday: '#2B59C3', edge_case: '#4E74CE', policy: '#12152A', adversarial: '#E8622A', tool_failure: '#B8410E', multi_turn: '#8A8FA6' }
const DEFAULT_MIX: Record<string, number> = { everyday: 8, edge_case: 6, policy: 4, adversarial: 3, tool_failure: 3, multi_turn: 2 }

function highlightRules(prompt: string) {
  const rule = /(never|always|only|must|do not|don't|before|cannot|can't|require)/i
  return prompt.split('\n').map((line, i) => (
    <span key={i}>{rule.test(line) ? <mark>{line}</mark> : line}{'\n'}</span>
  ))
}

export function SuiteNewPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const agent = useQuery({ queryKey: ['agent', id], queryFn: () => api.get<Agent>(`/api/v1/agents/${id}`) })
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.get<Meta>('/api/v1/meta'), staleTime: Infinity })
  const models = useQuery({ queryKey: ['models'], queryFn: () => api.get<Model[]>('/api/v1/models') })
  const a = agent.data
  const v = a?.versions?.[a.versions.length - 1]
  const hasTools = (v?.tools.length ?? 0) > 0
  const [mix, setMix] = useState(DEFAULT_MIX)
  const [name, setName] = useState('Generated suite')
  const [instructions, setInstructions] = useState('')
  const [sandbox, setSandbox] = useState('')
  const [gen, setGen] = useState('')
  const [importText, setImportText] = useState('')
  const [tab, setTab] = useState<'generate' | 'import'>('generate')
  const [err, setErr] = useState<string | null>(null)
  const effMix = useMemo(() => Object.fromEntries(Object.entries(mix).filter(([k, n]) => n > 0 && (hasTools || k !== 'tool_failure'))), [mix, hasTools])
  const total = Object.values(effMix).reduce((s, n) => s + n, 0)
  const genModel = models.data?.find((m) => m.id === gen) ?? models.data?.find((m) => m.options.roles?.includes('generator'))

  const go = useMutation({
    mutationFn: async () => {
      const s = await api.post<Suite>('/api/v1/suites', { agent_id: id, name })
      if (tab === 'generate') {
        await api.post(`/api/v1/suites/${s.id}/generate`, { mix: effMix, instructions: instructions || null, sandbox_data: sandbox || null, generator_model_id: gen || null })
      } else {
        let parsed: unknown
        try { parsed = JSON.parse(importText) } catch (e) { throw new Error(`Not valid JSON: ${(e as Error).message}`) }
        const tasks = Array.isArray(parsed) ? parsed : (parsed as { tasks?: unknown[] }).tasks
        if (!Array.isArray(tasks)) throw new Error('Expected a JSON array of tasks, or {"tasks": [...]}')
        const r = await api.post<{ imported: number; skipped: number }>(`/api/v1/suites/${s.id}/import`, { tasks })
        toast(`Imported ${r.imported} tasks${r.skipped ? `, skipped ${r.skipped} invalid rows` : ''}.`)
      }
      return s
    },
    onSuccess: (s) => { qc.invalidateQueries({ queryKey: ['suites', id] }); nav(`/suites/${s.id}`) },
    onError: (e: Error) => setErr(e.message),
  })

  if (!a) return <div className="page loading">Loading…</div>
  return (
    <div className="page">
      <div className="crumb"><Link to={`/agents/${a.id}`}>{a.name}</Link><span className="sep">/</span><span>New test suite</span></div>
      <div className="pagehead" style={{ marginTop: 18 }}>
        <div>
          <h1>What should we throw at {a.name}?</h1>
          <p>Aether reads the agent's instructions and tools and writes tasks aimed at the places it is most likely to break. You review them before running.</p>
        </div>
      </div>
      <div className="composer">
        <div>
          <div className="sec-h"><h2>What Aether read</h2></div>
          {v?.system_prompt ? <div className="prompt">{highlightRules(v.system_prompt)}</div> : <p className="ink2" style={{ marginTop: 14 }}>{a.kind === 'http' ? 'This agent runs behind its own endpoint, so Aether only knows its description: ' + (a.description || '(none)') : 'No instructions yet.'}</p>}
          {hasTools && (
            <div style={{ marginTop: 22 }}>
              <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{v!.tools.length} tools</h3>
              {v!.tools.map((t) => <div className="sig" key={t.name}><b>{t.name}</b>({Object.keys((t.parameters as { properties?: object }).properties ?? {}).join(', ')})</div>)}
            </div>
          )}
          <p className="small muted" style={{ marginTop: 14 }}>Highlighted rules become policy checks. Every tool becomes argument checks against its schema.</p>
        </div>
        <div>
          <div className="sec-h"><h2>What it will test</h2><div className="r seg"><button className={tab === 'generate' ? 'on' : ''} onClick={() => setTab('generate')}>Generate</button><button className={tab === 'import' ? 'on' : ''} onClick={() => setTab('import')}>Import JSON</button></div></div>
          <div className="plan-block">
            <label className="field">Suite name<input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
          </div>
          {tab === 'generate' ? (
            <>
              <div className="plan-block">
                <h3>Task mix, {total} tasks</h3>
                <div className="mixbar">
                  {Object.entries(effMix).map(([k, n]) => <div key={k} style={{ flex: n, background: COLORS[k] }}>{catLabel(k)}</div>)}
                </div>
                <div style={{ marginTop: 12 }}>
                  {Object.keys(meta.data?.task_categories ?? DEFAULT_MIX).map((k) => (
                    <div className="mixrow" key={k} title={meta.data?.task_categories[k]}>
                      <span>{catLabel(k)}<span className="small muted" style={{ display: 'block' }}>{meta.data?.task_categories[k]?.split('.')[0]}</span></span>
                      <input type="range" min={0} max={40} value={mix[k] ?? 0} disabled={k === 'tool_failure' && !hasTools} onChange={(e) => setMix({ ...mix, [k]: Number(e.target.value) })} aria-label={catLabel(k)} />
                      <span className="c fig">{k === 'tool_failure' && !hasTools ? '–' : mix[k] ?? 0}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="plan-block">
                <label className="field">Anything specific to cover?<span className="hint">Optional. e.g. "Focus on refunds for annual plans; customers often write in Spanish."</span><textarea className="textarea" style={{ minHeight: 70 }} value={instructions} onChange={(e) => setInstructions(e.target.value)} /></label>
                {v?.tools.some((t) => t.http?.url) && (
                  <label className="field" style={{ marginTop: 14 }}>Records in your test environment<span className="hint">For runs against real APIs. Paste the customers, orders or bookings your sandbox contains, so tasks only reference data that exists.</span>
                    <textarea className="textarea code" style={{ minHeight: 90 }} value={sandbox} onChange={(e) => setSandbox(e.target.value)} placeholder={'customers: dana@example.com (C-100), sam@example.com (C-200)\ninvoices C-100: INV-1001 $49 paid, INV-1002 $249 paid, INV-1003 $49 open'} />
                  </label>
                )}
                <label className="field" style={{ marginTop: 14 }}>Model that writes the tasks
                  <select className="select" value={gen} onChange={(e) => setGen(e.target.value)}>
                    <option value="">Workspace default{genModel && !gen ? ` (${genModel.display_name})` : ''}</option>
                    {(models.data ?? []).filter((m) => m.enabled).map((m) => <option key={m.id} value={m.id}>{m.display_name} ({m.provider_name})</option>)}
                  </select>
                </label>
              </div>
            </>
          ) : (
            <div className="plan-block">
              <label className="field">Tasks as JSON<span className="hint">An array of objects with "input", optional "category", "expected" (tool_calls, forbidden_tools, output_contains, assertions), "fixtures", "scenario", "max_turns". Use a suite's Export button for the exact format.</span>
                <textarea className="textarea code" style={{ minHeight: 260 }} value={importText} onChange={(e) => setImportText(e.target.value)} spellCheck={false}
                  placeholder={'[\n  {"category": "everyday", "input": "Where is order AB-1234?",\n   "expected": {"tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "AB-1234"}}]}}\n]'} />
              </label>
            </div>
          )}
          {err && <div className="error-box" style={{ marginTop: 14 }}>{err}</div>}
          <div className="row" style={{ paddingTop: 20 }}>
            <button className="btn ink" disabled={go.isPending || (tab === 'generate' && total === 0)} onClick={() => go.mutate()}>{tab === 'generate' ? `Generate ${total} tasks` : 'Import tasks'}</button>
            <span className="small muted">{tab === 'generate' ? 'Generation runs in the background; you can leave this page.' : ''}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
