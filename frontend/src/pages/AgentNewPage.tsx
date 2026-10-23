import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useToast } from '../components/ui'
import { api, type Agent, type Model } from '../lib/api'

type ToolDraft = { name: string; description: string; params: string; url: string; method: string }
const EXAMPLE_PARAMS = JSON.stringify({ type: 'object', properties: { order_id: { type: 'string', description: 'Order identifier' } }, required: ['order_id'] }, null, 2)
const newTool = (): ToolDraft => ({ name: '', description: '', params: EXAMPLE_PARAMS, url: '', method: 'POST' })

const PROTOCOL = `POST <your endpoint>
{ "protocol": "aether.agent.v1", "session_id": "...", "task_id": "...",
  "messages": [{ "role": "user", "content": "..." }, ...],
  "context": { "fixtures": {...}, "tool_mode": "simulated" } }

200 OK
{ "output": "final answer to the user",
  "steps": [{ "type": "tool_call", "name": "lookup_order",
              "arguments": {...}, "result": {...}, "latency_ms": 120 }],
  "usage": { "input_tokens": 812, "output_tokens": 95 }, "cost_usd": 0.0012 }`

function ToolEditor({ tools, setTools }: { tools: ToolDraft[]; setTools: (t: ToolDraft[]) => void }) {
  const upd = (i: number, patch: Partial<ToolDraft>) => setTools(tools.map((t, j) => (j === i ? { ...t, ...patch } : t)))
  return (
    <div style={{ display: 'grid', gap: 18 }}>
      {tools.map((t, i) => (
        <div key={i} style={{ borderTop: '1px solid var(--rule)', paddingTop: 14, display: 'grid', gap: 12 }}>
          <div className="cols" style={{ display: 'grid', gridTemplateColumns: '1fr 2fr auto', gap: 12, alignItems: 'end' }}>
            <label className="field">Name<input className="input code" value={t.name} onChange={(e) => upd(i, { name: e.target.value })} placeholder="lookup_order" /></label>
            <label className="field">What it does<input className="input" value={t.description} onChange={(e) => upd(i, { description: e.target.value })} placeholder="Find an order by its id" /></label>
            <button className="btn ghost danger" onClick={() => setTools(tools.filter((_, j) => j !== i))}>Remove</button>
          </div>
          <label className="field">Parameters (JSON schema)<textarea className="textarea code" value={t.params} onChange={(e) => upd(i, { params: e.target.value })} spellCheck={false} /></label>
          <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: 12 }}>
            <label className="field">Method<select className="select" value={t.method} onChange={(e) => upd(i, { method: e.target.value })}><option>POST</option><option>GET</option><option>PUT</option><option>DELETE</option></select></label>
            <label className="field">Real API URL<span className="hint">Optional. Used when a run calls real APIs. Use {'{param}'} for path parameters.</span><input className="input code" value={t.url} onChange={(e) => upd(i, { url: e.target.value })} placeholder="https://sandbox.example.com/orders/{order_id}" /></label>
          </div>
        </div>
      ))}
      <div><button className="btn" onClick={() => setTools([...tools, newTool()])}>Add a tool</button></div>
    </div>
  )
}

export function AgentNewPage() {
  const [params] = useSearchParams()
  const nav = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const models = useQuery({ queryKey: ['models'], queryFn: () => api.get<Model[]>('/api/v1/models') })
  const [mode, setMode] = useState<'builtin' | 'http' | 'openai_chat'>(params.get('kind') === 'external' ? 'http' : 'builtin')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [prompt, setPrompt] = useState('')
  const [tools, setTools] = useState<ToolDraft[]>([newTool()])
  const [modelId, setModelId] = useState('')
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [headers, setHeaders] = useState('')
  const [epModel, setEpModel] = useState('')
  const [err, setErr] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: (body: unknown) => api.post<Agent>('/api/v1/agents', body),
    onSuccess: (a) => { qc.invalidateQueries({ queryKey: ['agents'] }); toast(`${a.name} created.`); nav(`/agents/${a.id}/suites/new`) },
    onError: (e: Error) => setErr(e.message),
  })

  const submit = () => {
    setErr(null)
    if (!name.trim()) return setErr('Give the agent a name.')
    let toolDefs: unknown[] = []
    if (mode !== 'http') {
      try {
        toolDefs = tools.filter((t) => t.name.trim()).map((t) => ({
          name: t.name.trim(),
          description: t.description,
          parameters: JSON.parse(t.params || '{"type":"object","properties":{}}'),
          http: t.url ? { url: t.url, method: t.method } : null,
        }))
      } catch (e) {
        return setErr(`A tool's parameters are not valid JSON: ${(e as Error).message}`)
      }
    }
    let hdrs: Record<string, string> | undefined
    if (headers.trim()) {
      try { hdrs = JSON.parse(headers) } catch { return setErr('Headers must be a JSON object, e.g. {"X-Team": "search"}') }
    }
    const endpoint = mode === 'builtin' ? {} : { url, ...(apiKey ? { api_key: apiKey } : {}), ...(hdrs ? { headers: hdrs } : {}), ...(epModel ? { model: epModel } : {}), timeout_s: 120 }
    create.mutate({ name, description, kind: mode, endpoint, version: { system_prompt: mode === 'builtin' ? prompt : '', tools: toolDefs, default_model_id: modelId || null, params: { temperature: 0.2 } } })
  }
  const toolModels = (models.data ?? []).filter((m) => m.supports_tools && m.enabled)

  return (
    <div className="page">
      <div className="crumb"><Link to="/">Agents</Link><span className="sep">/</span><span>New agent</span></div>
      <div className="pagehead" style={{ marginTop: 14 }}>
        <div>
          <h1>{mode === 'builtin' ? 'Build an agent' : 'Connect an agent'}</h1>
          <p>{mode === 'builtin' ? 'Write its instructions and declare its tools. Aether runs it on any connected model.' : 'Aether sends test conversations to your endpoint and scores what comes back.'}</p>
        </div>
      </div>
      <div className="form" style={{ marginTop: 30 }}>
        <div className="choice">
          <button className={`opt ${mode === 'builtin' ? 'on' : ''}`} onClick={() => setMode('builtin')}><b>Build in Aether</b><span>Prompt, tools and a model. Compare models and prompt versions side by side.</span></button>
          <button className={`opt ${mode === 'http' ? 'on' : ''}`} onClick={() => setMode('http')}><b>Agent endpoint</b><span>Your agent runs its own loop and tools, and reports the steps it took.</span></button>
          <button className={`opt ${mode === 'openai_chat' ? 'on' : ''}`} onClick={() => setMode('openai_chat')}><b>OpenAI-compatible API</b><span>Your service speaks chat completions; Aether supplies tools and runs the loop.</span></button>
        </div>
        <div className="cols">
          <label className="field">Name<input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Order Support Agent" /></label>
          <label className="field">What it does<input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Answers order questions and issues refunds" /></label>
        </div>
        {mode === 'builtin' && (
          <>
            <label className="field">Instructions (system prompt)<span className="hint">Rules written here become policy checks in generated tests.</span>
              <textarea className="textarea" style={{ minHeight: 180 }} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={'You are the support agent for...\nRules:\n1. Verify the customer before sharing account details.\n2. ...'} />
            </label>
            <label className="field">Default model<span className="hint">Optional. You can pick several models for each run.</span>
              <select className="select" value={modelId} onChange={(e) => setModelId(e.target.value)}>
                <option value="">Choose per run</option>
                {toolModels.map((m) => <option key={m.id} value={m.id}>{m.display_name} ({m.provider_name})</option>)}
              </select>
            </label>
          </>
        )}
        {mode !== 'builtin' && (
          <>
            <label className="field">Endpoint URL<span className="hint">{mode === 'openai_chat' ? 'Base URL; Aether calls {url}/chat/completions.' : 'Aether POSTs each conversation turn here.'}</span><input className="input code" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={mode === 'openai_chat' ? 'https://my-agent.internal/v1' : 'https://my-agent.internal/aether'} /></label>
            <div className="cols">
              <label className="field">API key<span className="hint">Optional. Sent as a Bearer token; stored encrypted.</span><input className="input" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} /></label>
              {mode === 'openai_chat' ? (
                <label className="field">Model name<span className="hint">Value of the "model" field, if your API needs one.</span><input className="input" value={epModel} onChange={(e) => setEpModel(e.target.value)} /></label>
              ) : (
                <label className="field">Extra headers (JSON)<span className="hint">Optional, stored encrypted.</span><input className="input code" value={headers} onChange={(e) => setHeaders(e.target.value)} placeholder='{"X-Team": "search"}' /></label>
              )}
            </div>
            {mode === 'http' && (
              <div>
                <h3 style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Aether agent protocol</h3>
                <div className="ci">{PROTOCOL}</div>
              </div>
            )}
          </>
        )}
        {mode !== 'http' && (
          <div>
            <div className="sec-h" style={{ marginBottom: 6 }}><h2>Tools</h2><p>Aether validates every call against these schemas, and can simulate the responses.</p></div>
            <ToolEditor tools={tools} setTools={setTools} />
          </div>
        )}
        {err && <div className="error-box">{err}</div>}
        <div className="row">
          <button className="btn ink" disabled={create.isPending} onClick={submit}>{create.isPending ? 'Creating…' : 'Create agent and write tests'}</button>
          <Link className="btn ghost" to="/">Cancel</Link>
        </div>
      </div>
    </div>
  )
}
