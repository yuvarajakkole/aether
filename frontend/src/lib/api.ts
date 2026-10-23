export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const TOKEN_KEY = 'aether.token'
export const auth = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string | null) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY)),
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const token = auth.get()
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  if (res.status === 204) return undefined as T
  const text = await res.text()
  const data = text ? JSON.parse(text) : undefined
  if (!res.ok) {
    const detail = data?.detail
    const msg = Array.isArray(detail) ? detail.map((d: { loc?: string[]; msg: string }) => `${(d.loc ?? []).slice(1).join('.')}: ${d.msg}`).join('; ') : detail || res.statusText
    throw new ApiError(res.status, msg)
  }
  return data as T
}

/** Download a file from the API with the same credentials as other requests (plain links can't send them). */
export async function download(path: string, filename: string) {
  const token = auth.get()
  const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  if (!res.ok) throw new ApiError(res.status, `Download failed (HTTP ${res.status})`)
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const api = {
  get: <T>(p: string) => request<T>('GET', p),
  post: <T>(p: string, b?: unknown) => request<T>('POST', p, b ?? {}),
  patch: <T>(p: string, b: unknown) => request<T>('PATCH', p, b),
  del: (p: string) => request<void>('DELETE', p),
}

// ---------- types ----------
export type Meta = {
  app: string
  version: string
  org_name: string
  auth_mode: 'none' | 'api_key' | 'oidc'
  provider_presets: Preset[]
  task_categories: Record<string, string>
  default_gate: Gate
  gate_metrics: string[]
}
export type Preset = { id: string; label: string; kind: string; base_url: string | null; options: Record<string, unknown>; headers?: Record<string, string> }
export type Provider = { id: string; name: string; kind: string; base_url: string | null; api_key_set: boolean; api_key_hint: string | null; api_key_env: string | null; header_names: string[]; options: Record<string, unknown> }
export type Model = {
  id: string
  provider_id: string
  provider_name: string
  provider_kind: string
  name: string
  display_name: string
  input_cost_per_mtok: number
  output_cost_per_mtok: number
  rpm_limit: number | null
  tpm_limit: number | null
  supports_tools: boolean
  enabled: boolean
  options: { roles?: string[]; [k: string]: unknown }
}
export type ToolDef = { name: string; description: string; parameters: Record<string, unknown>; http?: { url: string; method?: string } | null }
export type Version = { id: string; version: number; label: string | null; system_prompt: string; tools: ToolDef[]; default_model_id: string | null; params: Record<string, unknown>; created_at: string }
export type GateRule = { metric: string; op: string; value: number; label?: string | null; actual?: number | null; passed?: boolean }
export type Gate = { name: string; rules: GateRule[]; passed?: boolean }
export type Agent = {
  id: string
  name: string
  slug: string
  description: string
  kind: 'builtin' | 'http' | 'openai_chat'
  endpoint: { url?: string; has_api_key?: boolean; header_names?: string[]; model?: string; timeout_s?: number }
  owner: string | null
  gate: Gate | null
  baseline_run_id: string | null
  versions?: Version[]
  latest_version?: Version | null
  version_count?: number
  suite_count?: number
  latest_run?: { id: string; number: number; finished_at: string; model_name: string | null; metrics: Partial<Metrics> | null; gate: Gate | null } | null
  last_activity?: { run_id: string; status: string; created_at: string } | null
  trend?: { run_id: string; number: number; all_pass_rate: number | null; pass_rate: number | null }[]
}
export type Task = {
  id: string
  ref: string
  seq: number
  category: string
  input: string
  scenario: string | null
  max_turns: number
  fixtures: Record<string, unknown>
  expected: {
    tool_calls?: { name: string; arguments?: Record<string, unknown> }[]
    forbidden_tools?: string[]
    output_contains?: string[]
    assertions?: string[]
  }
  inject: { tool?: string; kind?: string }
  tags: string[]
  enabled: boolean
}
export type Suite = {
  id: string
  agent_id: string
  name: string
  description: string
  status: 'draft' | 'generating' | 'ready' | 'failed'
  generation: { mix?: Record<string, number>; generated?: number; cost_usd?: number; warnings?: string[]; model?: string; requested?: number }
  error: string | null
  task_count: number | null
  categories: Record<string, number> | null
  created_at: string
  tasks?: Task[]
}
export type Metrics = {
  trials: number
  tasks: number
  attempts_per_task: number
  pass_rate: number | null
  all_pass_rate: number | null
  reliability_curve: { k: number; pass_hat_k: number | null; pass_at_k: number | null }[]
  tool_selection: number | null
  argument_accuracy: number | null
  schema_validity: number | null
  recovery_rate: number | null
  trials_with_tool_errors: number
  policy_pass_rate: number | null
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  input_tokens: number
  output_tokens: number
  cost_usd: number
  cost_per_trial_usd: number | null
  eval_cost_usd: number
  errors: number
  needs_review: number
  categories: Record<string, { tasks: number; pass_rate: number | null; all_pass_rate: number | null }>
  failure_categories: Record<string, number>
  task_pass_counts: Record<string, number>
}
export type Variant = {
  key: string
  model_id: string | null
  model_name: string
  provider: string | null
  version_id: string | null
  version: number | null
  version_label: string | null
  metrics: Metrics
  gate: Gate & { passed: boolean }
}
export type Run = {
  id: string
  number: number
  name: string | null
  agent_id: string
  suite_id: string
  status: 'queued' | 'running' | 'analyzing' | 'completed' | 'failed' | 'cancelled'
  config: { model_ids: (string | null)[]; version_ids: (string | null)[]; trials: number; tool_mode: string; failure_rate: number; judge_model_id: string | null; simulator_model_id: string | null; task_count: number }
  created_by: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
  summary?: {
    variants: Record<string, Variant>
    progress: Record<string, number>
    totals: { cost_usd: number; eval_cost_usd: number; trials: number; passed: number }
    recommendation: { headline: string; ship?: string | null; picks: Record<string, { variant: string; value: number | null } | null> }
  }
  failure_clusters?: { name: string; description: string; trial_ids: string[]; by_variant: Record<string, number> }[]
  headline?: string | null
  totals?: { cost_usd: number; eval_cost_usd: number; trials: number; passed: number } | null
  variants?: Record<string, { model_name: string; version: number | null; pass_rate: number | null; all_pass_rate: number | null; gate_passed: boolean }>
}
export type Cell = { trial_id: string; state: 'pass' | 'fail' | 'queued' | 'running' | 'cancelled'; failure: string | null } | null
export type Matrix = { variant: string; variants: string[]; trials_per_task: number; tasks: { task_id: string; ref: string; category: string; input: string; attempts: Cell[] }[] }
export type TraceStep = {
  type: 'user' | 'assistant' | 'tool_call' | 'tool_result' | 'error' | 'summary'
  t_ms?: number
  wall_ms?: number
  content?: string
  name?: string
  arguments?: Record<string, unknown>
  result?: unknown
  latency_ms?: number
  simulated?: boolean
  injected?: boolean
  interim?: boolean
  external?: boolean
  invalid_args?: string[]
  parse_error?: string
}
export type Check = { name: string; passed: boolean; detail: string; gating: boolean }
export type Trial = {
  id: string
  run_id: string
  task_id: string
  variant: string
  trial_index: number
  status: string
  passed: boolean | null
  effective_passed: boolean | null
  score: number | null
  failure_category: string | null
  latency_ms: number | null
  input_tokens: number
  output_tokens: number
  cost_usd: number
  eval_cost_usd: number
  needs_review: boolean
  human_verdict: 'pass' | 'fail' | null
  error: string | null
  checks?: Check[]
  judge?: { passed?: boolean; reasoning?: string; failure_label?: string | null; confidence?: number; assertions?: { assertion: string; passed: boolean; reason: string }[]; policy_violations?: string[]; unsupported_claims?: string[]; model?: string; error?: string }
  trace?: TraceStep[]
  output?: string | null
  human_note?: string | null
  reviewed_by?: string | null
  task?: Task
  attempts?: { id: string; index: number; passed: boolean | null; status: string }[]
}
export type FeedItem = { kind: string; at: string; text: string; agent_id?: string; run_id?: string; suite_id?: string; model_id?: string; count?: number }
export type ReviewItem = Trial & { task_input: string; category: string; run_number: number; agent_id: string }
export type Compare = {
  base: { run_id: string; number: number; variant: string; metrics: Partial<Metrics> }
  head: { run_id: string; number: number; variant: string; metrics: Partial<Metrics> }
  deltas: Record<string, number | null>
  regressions: { task_id: string; input: string; category: string; base: [number, number]; head: [number, number] }[]
  improvements: { task_id: string; input: string; category: string; base: [number, number]; head: [number, number] }[]
  unchanged: number
}
