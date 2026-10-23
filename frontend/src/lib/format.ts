export const pct = (v: number | null | undefined, digits = 0) => (v === null || v === undefined || Number.isNaN(v) ? '–' : `${(v * 100).toFixed(digits)}%`)

export const secs = (ms: number | null | undefined) => (ms === null || ms === undefined ? '–' : ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`)

export function money(v: number | null | undefined) {
  if (v === null || v === undefined) return '–'
  if (v === 0) return '$0'
  if (v < 0.01) return `${(v * 100).toFixed(2)}¢`
  if (v < 1) return `${(v * 100).toFixed(1)}¢`
  return `$${v.toFixed(2)}`
}

export const num = (v: number | null | undefined) => (v === null || v === undefined ? '–' : v.toLocaleString())

export function ago(iso: string | null | undefined) {
  if (!iso) return '–'
  const t = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').getTime()
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`
  return new Date(t).toLocaleDateString()
}

export function duration(a: string | null | undefined, b: string | null | undefined) {
  if (!a) return '–'
  const toMs = (x: string) => new Date(x.endsWith('Z') || x.includes('+') ? x : x + 'Z').getTime()
  const ms = (b ? toMs(b) : Date.now()) - toMs(a)
  const m = Math.floor(ms / 60000)
  return m < 1 ? `${Math.round(ms / 1000)}s` : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`
}

export const CATEGORY_LABEL: Record<string, string> = {
  everyday: 'Everyday requests',
  edge_case: 'Edge cases',
  policy: 'Policy and compliance',
  adversarial: 'Prompt injection and abuse',
  tool_failure: 'Tool failures',
  multi_turn: 'Multi-turn conversations',
}
export const catLabel = (c: string) => CATEGORY_LABEL[c] ?? c

export const CHECK_LABEL: Record<string, string> = {
  completed: 'Finished with an answer',
  tool_selection: 'Chose the right tools',
  forbidden_tools: 'Avoided forbidden tools',
  unknown_tools: 'Only used tools that exist',
  arguments: 'Arguments match the request',
  schema: 'Arguments match the tool schema',
  output: 'Answer contains required facts',
  recovery: 'Recovered from tool errors',
  efficiency: 'No loops or wasted steps',
  judge: 'Judge: requirements and policy',
}

/** Blend from fail (orange) to pass (blue) for n of total. */
export function passColor(n: number, total: number) {
  const t = total ? n / total : 0
  const a = [43, 89, 195]
  const b = [232, 98, 42]
  return `rgb(${a.map((v, i) => Math.round(b[i] + (v - b[i]) * t)).join(',')})`
}
