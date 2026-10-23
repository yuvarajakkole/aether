import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Dialog, useToast } from '../components/ui'
import { api, type Meta, type Model, type Provider } from '../lib/api'
import { num, secs } from '../lib/format'

type TestResult = { ok: boolean; reply?: string; latency_ms?: number; error?: string; tool_call?: { ok: boolean; called: string[] } }

function ProviderDialog({ meta, onClose }: { meta?: Meta; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [preset, setPreset] = useState(meta?.provider_presets[0])
  const [name, setName] = useState('')
  const [baseUrl, setBaseUrl] = useState(preset?.base_url ?? '')
  const [key, setKey] = useState('')
  const [keyEnv, setKeyEnv] = useState('')
  const [headers, setHeaders] = useState(preset?.headers ? JSON.stringify(preset.headers) : '')
  const [err, setErr] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (body: unknown) => api.post<Provider>('/api/v1/providers', body),
    onSuccess: (p) => { qc.invalidateQueries({ queryKey: ['providers'] }); toast(`${p.name} added. Now add its models.`); onClose() },
    onError: (e: Error) => setErr(e.message),
  })
  const submit = () => {
    let h = {}
    if (headers.trim()) {
      try { h = JSON.parse(headers) } catch { return setErr('Headers must be a JSON object') }
    }
    save.mutate({ name: name || preset?.id, kind: preset?.kind, base_url: baseUrl || null, api_key: key || null, api_key_env: keyEnv || null, headers: h, options: preset?.options ?? {} })
  }
  return (
    <Dialog title="Connect a provider" onClose={onClose} footer={<><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn ink" onClick={submit} disabled={save.isPending}>Connect</button></>}>
      <div className="form">
        <label className="field">Provider
          <select className="select" value={preset?.id} onChange={(e) => { const p = meta?.provider_presets.find((x) => x.id === e.target.value); setPreset(p); setBaseUrl(p?.base_url ?? ''); setHeaders(p?.headers ? JSON.stringify(p.headers) : '') }}>
            {meta?.provider_presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </label>
        <label className="field">Name<span className="hint">How it appears in model lists, e.g. "openai-prod".</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={preset?.id} /></label>
        {preset?.kind !== 'mock' && <label className="field">Base URL<input className="input code" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></label>}
        {preset?.kind !== 'mock' && (
          <div className="cols">
            <label className="field">API key<span className="hint">Encrypted at rest, never shown again.</span><input className="input" type="password" value={key} onChange={(e) => setKey(e.target.value)} /></label>
            <label className="field">or environment variable<span className="hint">Read from the server at call time.</span><input className="input code" value={keyEnv} onChange={(e) => setKeyEnv(e.target.value)} placeholder="OPENAI_API_KEY" /></label>
          </div>
        )}
        {preset?.kind !== 'mock' && <label className="field">Extra headers (JSON)<span className="hint">For gateways that need an app or tenant header.</span><input className="input code" value={headers} onChange={(e) => setHeaders(e.target.value)} placeholder='{"x-client-app": "my-app"}' /></label>}
        {err && <div className="error-box">{err}</div>}
      </div>
    </Dialog>
  )
}

function ModelDialog({ providers, onClose }: { providers: Provider[]; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [f, setF] = useState({ provider_id: providers[0]?.id ?? '', name: '', display_name: '', input_cost_per_mtok: 0, output_cost_per_mtok: 0, rpm_limit: '', tpm_limit: '', supports_tools: true, reasoning: false })
  const [err, setErr] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: () => api.post<Model>('/api/v1/models', {
      provider_id: f.provider_id, name: f.name, display_name: f.display_name || null, input_cost_per_mtok: Number(f.input_cost_per_mtok), output_cost_per_mtok: Number(f.output_cost_per_mtok),
      rpm_limit: f.rpm_limit ? Number(f.rpm_limit) : null, tpm_limit: f.tpm_limit ? Number(f.tpm_limit) : null, supports_tools: f.supports_tools,
      options: f.reasoning ? { no_temperature: true, max_tokens_param: 'max_completion_tokens' } : {},
    }),
    onSuccess: (m) => { qc.invalidateQueries({ queryKey: ['models'] }); toast(`${m.display_name} added. Test its connection next.`); onClose() },
    onError: (e: Error) => setErr(e.message),
  })
  const set = (k: string, v: unknown) => setF({ ...f, [k]: v })
  return (
    <Dialog title="Add a model" onClose={onClose} footer={<><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn ink" onClick={() => save.mutate()} disabled={!f.name || save.isPending}>Add model</button></>}>
      <div className="form">
        <label className="field">Provider<select className="select" value={f.provider_id} onChange={(e) => set('provider_id', e.target.value)}>{providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <div className="cols">
          <label className="field">Model id<span className="hint">Exactly as the provider names it.</span><input className="input code" value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="gpt-4o-mini" /></label>
          <label className="field">Display name<input className="input" value={f.display_name} onChange={(e) => set('display_name', e.target.value)} /></label>
        </div>
        <div className="cols">
          <label className="field">Input $ per 1M tokens<input className="input" type="number" step="0.01" value={f.input_cost_per_mtok} onChange={(e) => set('input_cost_per_mtok', e.target.value)} /></label>
          <label className="field">Output $ per 1M tokens<input className="input" type="number" step="0.01" value={f.output_cost_per_mtok} onChange={(e) => set('output_cost_per_mtok', e.target.value)} /></label>
        </div>
        <div className="cols">
          <label className="field">Requests per minute<span className="hint">Your quota; Aether stays under it.</span><input className="input" type="number" value={f.rpm_limit} onChange={(e) => set('rpm_limit', e.target.value)} /></label>
          <label className="field">Tokens per minute<input className="input" type="number" value={f.tpm_limit} onChange={(e) => set('tpm_limit', e.target.value)} /></label>
        </div>
        <label className="check"><input type="checkbox" checked={f.supports_tools} onChange={(e) => set('supports_tools', e.target.checked)} />Supports tool calling</label>
        <label className="check"><input type="checkbox" checked={f.reasoning} onChange={(e) => set('reasoning', e.target.checked)} />Reasoning model (no temperature, uses max_completion_tokens)</label>
        {err && <div className="error-box">{err}</div>}
      </div>
    </Dialog>
  )
}

export function ModelsPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.get<Meta>('/api/v1/meta'), staleTime: Infinity })
  const providers = useQuery({ queryKey: ['providers'], queryFn: () => api.get<Provider[]>('/api/v1/providers') })
  const models = useQuery({ queryKey: ['models'], queryFn: () => api.get<Model[]>('/api/v1/models') })
  const [dialog, setDialog] = useState<'provider' | 'model' | null>(null)
  const [tests, setTests] = useState<Record<string, TestResult | 'running'>>({})
  const test = async (m: Model) => {
    setTests((t) => ({ ...t, [m.id]: 'running' }))
    try {
      const r = await api.post<TestResult>(`/api/v1/models/${m.id}/test`)
      setTests((t) => ({ ...t, [m.id]: r }))
    } catch (e) {
      setTests((t) => ({ ...t, [m.id]: { ok: false, error: (e as Error).message } }))
    }
  }
  const toggle = useMutation({
    mutationFn: (m: Model) => api.patch(`/api/v1/models/${m.id}`, { enabled: !m.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
    onError: (e: Error) => toast(e.message, true),
  })
  const role = useMutation({
    mutationFn: async ({ m, r }: { m: Model; r: string }) => {
      for (const other of models.data ?? []) {
        if (other.id !== m.id && other.options.roles?.includes(r)) await api.patch(`/api/v1/models/${other.id}`, { options: { ...other.options, roles: other.options.roles.filter((x) => x !== r) } })
      }
      const roles = m.options.roles?.includes(r) ? m.options.roles.filter((x) => x !== r) : [...(m.options.roles ?? []), r]
      return api.patch(`/api/v1/models/${m.id}`, { options: { ...m.options, roles } })
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
  })
  return (
    <div className="page">
      <div className="pagehead">
        <div>
          <h1>Models</h1>
          <p>Any provider works: OpenAI, Anthropic, Azure, Bedrock, an internal LLM gateway or a self-hosted server. Aether also uses three of these models itself, to write tasks, simulate tools and users, and judge results.</p>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => setDialog('provider')}>Connect a provider</button>
          <button className="btn ink" onClick={() => setDialog('model')} disabled={!providers.data?.length}>Add a model</button>
        </div>
      </div>
      {(providers.data ?? []).map((p) => {
        const ms = (models.data ?? []).filter((m) => m.provider_id === p.id)
        return (
          <div className="sec" key={p.id}>
            <div className="sec-h">
              <h2>{p.name}</h2>
              <p>{p.kind.replace('_', ' ')}{p.base_url ? `, ${p.base_url}` : ''}</p>
              <span className="r small muted">{p.api_key_set ? (p.api_key_env ? `key from $${p.api_key_env}` : `key ${p.api_key_hint}`) : p.kind === 'mock' ? 'offline' : 'no key'}{p.header_names.length ? `, headers: ${p.header_names.join(', ')}` : ''}</span>
            </div>
            <table className="table" style={{ tableLayout: 'fixed' }}>
              <colgroup><col style={{ width: '22%' }} /><col style={{ width: '12%' }} /><col style={{ width: '15%' }} /><col style={{ width: '6%' }} /><col style={{ width: '20%' }} /><col style={{ width: '17%' }} /><col style={{ width: '8%' }} /></colgroup>
              <thead><tr><th>Model</th><th className="num">$ in / out per 1M</th><th className="num">Quota per minute</th><th>Tools</th><th>Aether uses it to</th><th>Connection</th><th /></tr></thead>
              <tbody>
                {ms.map((m) => {
                  const t = tests[m.id]
                  return (
                    <tr key={m.id} style={{ opacity: m.enabled ? 1 : 0.5 }}>
                      <td><b>{m.display_name}</b>{m.display_name !== m.name && <div className="small muted code">{m.name}</div>}</td>
                      <td className="num fig">{m.input_cost_per_mtok || m.output_cost_per_mtok ? `$${m.input_cost_per_mtok} / $${m.output_cost_per_mtok}` : '–'}</td>
                      <td className="num small">{m.rpm_limit ? `${num(m.rpm_limit)} req` : ''}{m.tpm_limit ? `, ${num(m.tpm_limit)} tok` : ''}{!m.rpm_limit && !m.tpm_limit ? '–' : ''}</td>
                      <td className="small">{m.supports_tools ? 'Yes' : 'No'}</td>
                      <td>
                        <div className="row" style={{ gap: 4 }}>
                          {['generator', 'simulator', 'judge'].map((r) => (
                            <button key={r} className={`tag ${m.options.roles?.includes(r) ? 'mark' : ''}`} onClick={() => role.mutate({ m, r })} title={`Make this the default ${r}`}>{{ generator: 'write tasks', simulator: 'simulate', judge: 'judge' }[r]}</button>
                          ))}
                        </div>
                      </td>
                      <td className="small">
                        {t === 'running' ? <span className="status live">Testing…</span>
                          : t ? (t.ok ? <span className="status">Works, {secs(t.latency_ms)}{t.tool_call ? ', tool call ok' : ''}</span> : <span className="status bad" title={t.error}>{(t.error ?? 'failed').slice(0, 80)}</span>)
                          : <button className="btn sm" onClick={() => test(m)}>Test</button>}
                      </td>
                      <td><button className="btn ghost sm" onClick={() => toggle.mutate(m)}>{m.enabled ? 'Disable' : 'Enable'}</button></td>
                    </tr>
                  )
                })}
                {ms.length === 0 && <tr><td colSpan={7} className="muted">No models yet.</td></tr>}
              </tbody>
            </table>
          </div>
        )
      })}
      {dialog === 'provider' && <ProviderDialog meta={meta.data} onClose={() => setDialog(null)} />}
      {dialog === 'model' && providers.data && <ModelDialog providers={providers.data} onClose={() => setDialog(null)} />}
    </div>
  )
}
