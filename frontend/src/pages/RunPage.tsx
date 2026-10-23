import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { TaskField } from '../components/TaskField'
import { TrialDrawer } from '../components/TrialDrawer'
import { RunStatus, Strip, useToast } from '../components/ui'
import { api, download, type Agent, type Matrix, type Model, type Run, type Suite, type Variant } from '../lib/api'
import { ago, duration, money, num, pct, secs } from '../lib/format'

const LINE = ['#C99A00', '#12152A', '#2B59C3', '#8A8FA6', '#7F9DE0', '#E8622A']
const CAUSE = ['#B8410E', '#E8622A', '#F4A47F', '#F8CDB5', '#E9EBF1', '#D5D8E3']

function label(v: Variant, many: boolean) {
  return many && v.version ? `${v.model_name}, prompt v${v.version}` : v.model_name
}

function Curve({ variants, many }: { variants: Variant[]; many: boolean }) {
  const k = Math.max(...variants.map((v) => v.metrics.reliability_curve.length), 1)
  if (k < 2) return null
  const W = 580, H = 210, L = 40, B = 190
  const x = (i: number) => L + ((i - 1) * (W - L - 20)) / (k - 1)
  const y = (v: number) => B - v * (B - 10)
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H + 18}`} width="100%" role="img" aria-label="Reliability curve: chance of passing every one of k attempts">
        {[0, 0.2, 0.4, 0.6, 0.8, 1].map((g) => (
          <g key={g}>
            <line x1={L} x2={W - 20} y1={y(g)} y2={y(g)} stroke="#E6E8EF" />
            <text x={L - 8} y={y(g) + 4} fontSize="11" textAnchor="end" fill="#8A8FA6">{Math.round(g * 100)}%</text>
          </g>
        ))}
        {Array.from({ length: k }, (_, i) => (
          <text key={i} x={x(i + 1)} y={H + 12} fontSize="11" textAnchor="middle" fill="#8A8FA6">{i + 1 === k ? `${k} attempts` : i + 1}</text>
        ))}
        {variants.map((v, vi) => {
          const pts = v.metrics.reliability_curve.filter((p) => p.pass_hat_k !== null)
          return <polyline key={v.key} fill="none" stroke={LINE[vi % LINE.length]} strokeWidth={vi === 0 ? 3 : 2.2} points={pts.map((p) => `${x(p.k)},${y(p.pass_hat_k!)}`).join(' ')} />
        })}
      </svg>
      <div className="curve-legend">
        {variants.map((v, vi) => (
          <span key={v.key}><i style={{ background: LINE[vi % LINE.length] }} />{label(v, many)}</span>
        ))}
      </div>
    </div>
  )
}

export function RunPage() {
  const { id } = useParams()
  const qc = useQueryClient()
  const toast = useToast()
  const run = useQuery({
    queryKey: ['run', id],
    queryFn: () => api.get<Run>(`/api/v1/runs/${id}`),
    refetchInterval: (q) => (q.state.data && ['queued', 'running', 'analyzing'].includes(q.state.data.status) ? 4000 : false),
  })
  const r = run.data
  const live = !!r && ['queued', 'running', 'analyzing'].includes(r.status)
  const agent = useQuery({ queryKey: ['agent', r?.agent_id], queryFn: () => api.get<Agent>(`/api/v1/agents/${r!.agent_id}`), enabled: !!r })
  const suite = useQuery({ queryKey: ['suite-meta', r?.suite_id], queryFn: () => api.get<Suite>(`/api/v1/suites/${r!.suite_id}`), enabled: !!r })
  const models = useQuery({ queryKey: ['models'], queryFn: () => api.get<Model[]>('/api/v1/models') })
  const variants = useMemo(() => Object.values(r?.summary?.variants ?? {}).filter((v) => v.metrics), [r])
  const many = new Set(variants.map((v) => v.version)).size > 1
  const [sel, setSel] = useState<string | null>(null)
  const ship = r?.summary?.recommendation?.ship
  const current = sel ?? ship ?? variants[0]?.key ?? null
  const matrix = useQuery({
    queryKey: ['matrix', id, current],
    queryFn: () => api.get<Matrix>(`/api/v1/runs/${id}/matrix?variant=${encodeURIComponent(current!)}`),
    enabled: !!current,
    refetchInterval: live ? 5000 : false,
  })
  const [search, setSearch] = useSearchParams()
  const trialParam = search.get('trial')
  const [openTask, setOpenTask] = useState<string | null>(null)
  const open = trialParam ? { task: openTask, trial: trialParam } : null
  const setOpen = (o: { task: string; trial: string } | null) => {
    setOpenTask(o?.task ?? null)
    const next = new URLSearchParams(search)
    if (o) next.set('trial', o.trial)
    else next.delete('trial')
    setSearch(next, { replace: true })
  }
  const prevVariant = useRef(current)
  useEffect(() => {
    if (prevVariant.current && prevVariant.current !== current) setOpen(null)
    prevVariant.current = current
  }, [current]) // eslint-disable-line react-hooks/exhaustive-deps

  const act = useMutation({
    mutationFn: (path: string) => api.post(`/api/v1/runs/${id}/${path}`),
    onSuccess: (_, path) => {
      qc.invalidateQueries({ queryKey: ['run', id] })
      qc.invalidateQueries({ queryKey: ['matrix', id] })
      toast({ cancel: 'Run cancelled.', reanalyze: 'Metrics recomputed with human verdicts.', 'retry-errors': 'Errored attempts re-queued.' }[path] ?? 'Done.')
    },
    onError: (e: Error) => toast(e.message, true),
  })
  const baseline = useMutation({
    mutationFn: () => api.post(`/api/v1/agents/${r!.agent_id}/baseline/${r!.id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['agent', r?.agent_id] }); toast('Set as the baseline for future comparisons.') },
    onError: (e: Error) => toast(e.message, true),
  })

  if (run.isLoading) return <div className="page loading">Loading run…</div>
  if (run.error || !r) return <div className="page"><div className="error-box">{(run.error as Error)?.message ?? 'Run not found'}</div></div>

  const prog = r.summary?.progress ?? {}
  const finished = (prog.done ?? 0) + (prog.error ?? 0)
  const total = prog.total ?? r.summary?.totals.trials ?? 0
  const m = (mid: string | null) => models.data?.find((x) => x.id === mid)
  const cur = variants.find((v) => v.key === current)
  const clusters = (r.failure_clusters ?? []).map((c) => ({ ...c, n: current ? c.by_variant?.[current] ?? 0 : c.trial_ids.length })).filter((c) => c.n > 0)
  const failedTotal = clusters.reduce((s, c) => s + c.n, 0)
  const agentRef = agent.data?.slug ?? 'my-agent'
  const modelRef = cur?.model_id ? `${m(cur.model_id)?.provider_name}/${m(cur.model_id)?.name}` : ''

  return (
    <div className="page">
      <div className="row" style={{ flexWrap: 'wrap' }}>
        <div className="crumb">
          <Link to={`/agents/${r.agent_id}`}>{agent.data?.name ?? 'Agent'}</Link>
          <span className="sep">/</span>
          <Link to="/runs">Runs</Link>
          <span className="sep">/</span>
          <span>{r.number}</span>
        </div>
        <RunStatus status={r.status} />
        <span className="small muted">{r.finished_at ? `finished ${ago(r.finished_at)}` : r.started_at ? `started ${ago(r.started_at)}` : `queued ${ago(r.created_at)}`}</span>
        <div className="spacer row">
          {live && <button className="btn ghost" onClick={() => act.mutate('cancel')}>Cancel run</button>}
          {!live && (prog.error ?? 0) > 0 && <button className="btn" onClick={() => act.mutate('retry-errors')}>Retry {prog.error} errored attempts</button>}
          {r.status === 'completed' && <button className="btn ghost" onClick={() => act.mutate('reanalyze')}>Re-analyse</button>}
          {r.status === 'completed' && variants.length > 1 && <Link className="btn" to={`/compare?base=${r.id}&head=${r.id}`}>Compare variants task by task</Link>}
          {r.status === 'completed' && (
            <button className="btn" onClick={() => download(`/api/v1/runs/${r.id}/export.csv`, `aether-run-${r.number}.csv`).catch((e: Error) => toast(e.message, true))}>Export CSV</button>
          )}
          {r.status === 'completed' && (
            agent.data?.baseline_run_id === r.id
              ? <span className="status">Baseline</span>
              : <button className="btn ink" onClick={() => baseline.mutate()}>Set as baseline</button>
          )}
        </div>
      </div>

      <div className="verdict">
        <div>
          <h1>{live ? (r.status === 'analyzing' ? 'All attempts finished. Grouping failures and checking gates…' : `Running ${num(total)} attempts…`) : r.summary?.recommendation.headline ?? r.error ?? 'No results.'}</h1>
          <p className="runmeta">
            {r.config.task_count} tasks from <Link className="link" to={`/suites/${r.suite_id}`}>{suite.data?.name ?? 'the suite'}</Link>, each attempted {r.config.trials} times
            {variants.length > 1 ? ` by ${variants.length} variants` : ''}. Tools were {r.config.tool_mode === 'real' ? 'real API calls' : 'simulated'}
            {r.config.failure_rate ? `, with errors injected into ${Math.round(r.config.failure_rate * 100)}% of calls` : ''}. Scored by rules first, then by {m(r.config.judge_model_id)?.display_name ?? 'the workspace judge model'}.
          </p>
          {live && total > 0 && (
            <div style={{ marginTop: 16, maxWidth: 560 }}>
              <div className="progress"><i style={{ width: `${(prog.done ?? 0) / total * 100}%`, background: 'var(--pass)' }} /><i style={{ width: `${(prog.error ?? 0) / total * 100}%`, background: 'var(--fail)' }} /><i style={{ width: `${(prog.running ?? 0) / total * 100}%`, background: 'var(--pass-2)' }} /></div>
              <p className="small muted" style={{ marginTop: 6 }}>{finished} of {total} finished, {prog.running ?? 0} running, {duration(r.started_at, null)} elapsed</p>
            </div>
          )}
        </div>
        <dl className="facts">
          <dt>Variants</dt><dd>{variants.length || r.config.model_ids.length * Math.max(1, r.config.version_ids.length)}</dd>
          <dt>Attempts</dt><dd className="fig">{num(total)}</dd>
          <dt>Duration</dt><dd>{duration(r.started_at, r.finished_at)}</dd>
          <dt>Agent spend</dt><dd className="fig">{money(r.summary?.totals.cost_usd)}</dd>
          <dt>Evaluation spend</dt><dd className="fig">{money(r.summary?.totals.eval_cost_usd)}</dd>
          {cur && cur.metrics.trials > 0 && <><dt>Release gate</dt><dd><span className={`status ${cur.gate.passed ? '' : 'bad'}`}>{cur.gate.passed ? 'Passes' : 'Fails'} for {cur.model_name}</span></dd></>}
        </dl>
      </div>

      {variants.length > 0 && (
        <div className="sec">
          <div className="sec-h"><h2>Variants</h2><p>Each strip shows every task, sorted from always solved to never solved. Click a row to inspect it.</p></div>
          <div style={{ overflowX: 'auto' }}>
            <table className="models">
              <thead>
                <tr>
                  <th className="l" style={{ width: 200 }}>Model</th>
                  <th className="l">Task outcomes across {r.config.trials} attempts</th>
                  <th>Attempts passed</th>
                  <th>Passed all {r.config.trials}</th>
                  <th className="hide-s">Right tools</th>
                  <th className="hide-s">Right arguments</th>
                  <th className="hide-s">Recovered</th>
                  <th className="hide-s">p95 latency</th>
                  <th>Cost per attempt</th>
                </tr>
              </thead>
              <tbody>
                {variants.map((v) => {
                  const best = (k: 'pass_rate' | 'all_pass_rate') => variants.length > 1 && v.metrics[k] === Math.max(...variants.map((x) => x.metrics[k] ?? 0))
                  return (
                    <tr key={v.key} className={`rowc ${v.key === current ? 'sel' : ''}`} onClick={() => setSel(v.key)}>
                      <td className="l"><div className="mname">{label(v, many)}<small>{v.provider}{v.key === ship ? ', recommended' : ''}</small></div></td>
                      <td className="l" style={{ width: '24%' }}><Strip counts={Object.values(v.metrics.task_pass_counts)} total={v.metrics.attempts_per_task} /></td>
                      <td className={`fig big ${best('pass_rate') ? 'lead' : ''}`}><span>{pct(v.metrics.pass_rate, 1)}</span></td>
                      <td className={`fig big ${best('all_pass_rate') ? 'lead' : ''}`}><span>{pct(v.metrics.all_pass_rate, 1)}</span></td>
                      <td className="fig v hide-s">{pct(v.metrics.tool_selection)}</td>
                      <td className="fig v hide-s">{pct(v.metrics.argument_accuracy)}</td>
                      <td className="fig v hide-s" title={`${v.metrics.trials_with_tool_errors} attempts hit a tool error`}>{pct(v.metrics.recovery_rate)}</td>
                      <td className="fig v hide-s">{secs(v.metrics.latency_p95_ms)}</td>
                      <td className="fig v">{money(v.metrics.cost_per_trial_usd)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {variants.length > 1 && variants[0].metrics.attempts_per_task > 1 && (
            <div className="two" style={{ marginTop: 28 }}>
              <Curve variants={variants} many={many} />
              <div className="small ink2" style={{ alignSelf: 'center', maxWidth: '46ch' }}>
                <b style={{ color: 'var(--ink)' }}>Reliability curve.</b> The chance a variant succeeds on every one of k attempts at the same task (pass^k).
                A line that stays flat belongs to an agent you can depend on; a steep drop means it solves tasks by luck.
              </div>
            </div>
          )}
        </div>
      )}

      <div className="sec">
        <div className="sec-h"><h2>Task field</h2><p>One tile per task, one square per attempt. Click a tile to replay it.</p></div>
        <div className="field-ctl">
          {variants.length > 1 && (
            <div className="seg">
              {variants.map((v) => <button key={v.key} className={v.key === current ? 'on' : ''} onClick={() => setSel(v.key)}>{label(v, many)}</button>)}
            </div>
          )}
          <div className="key">
            <span><i style={{ background: 'var(--pass)' }} />Attempt passed</span>
            <span><i style={{ background: 'var(--fail)' }} />Attempt failed</span>
            {live && <span><i style={{ background: 'var(--empty)' }} />Not run yet</span>}
            <span>Solid blue tiles are dependable. Speckled tiles are flaky.</span>
          </div>
        </div>
        {matrix.data ? <TaskField matrix={matrix.data} selectedTask={open?.task} onOpen={(task, trial) => trial && setOpen({ task, trial })} /> : <div className="loading">Loading tasks…</div>}
      </div>

      {r.status === 'completed' && cur && (
        <div className="sec two">
          <div>
            <div className="sec-h"><h2>Where {cur.model_name} breaks</h2><p>{failedTotal} failed attempts, grouped by cause</p></div>
            {clusters.length === 0 ? (
              <p className="ink2" style={{ marginTop: 16 }}>No failed attempts for this variant.</p>
            ) : (
              <>
                <div className="stack">{clusters.map((c, i) => <div key={c.name} style={{ flex: c.n, background: CAUSE[i % CAUSE.length] }} />)}</div>
                <div className="causes">
                  {clusters.map((c, i) => (
                    <div className="cause" key={c.name}>
                      <span className="sw" style={{ background: CAUSE[i % CAUSE.length] }} />
                      <b>{c.name}</b>
                      <span className="c fig">{c.n}</span>
                      <p>{c.description}</p>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
          <div>
            <div className="sec-h"><h2>Release gate</h2><p>{cur.gate.name}</p><span className={`r status ${cur.gate.passed ? '' : 'bad'}`}>{cur.gate.passed ? 'Passes' : 'Fails'}</span></div>
            <div>
              {cur.gate.rules.map((g) => {
                const lower = g.op.startsWith('<')
                const isRate = g.value <= 1 && !g.metric.includes('latency') && !g.metric.includes('cost')
                const fmt = (v: number | null | undefined) => (v === null || v === undefined ? '–' : g.metric.includes('latency') ? secs(v) : g.metric.includes('cost') ? money(v) : isRate ? pct(v, 1) : String(v))
                const scale = isRate ? 1 : Math.max(g.value, g.actual ?? 0) * 1.25 || 1
                return (
                  <div className="grow-row" key={g.metric}>
                    <span>{g.label ?? g.metric}, {lower ? 'at most' : 'at least'} {fmt(g.value)}</span>
                    <span className="gbar"><i style={{ width: `${Math.min(100, ((g.actual ?? 0) / scale) * 100)}%`, background: g.passed ? 'var(--pass)' : 'var(--fail)' }} /><u style={{ left: `${Math.min(100, (g.value / scale) * 100)}%` }} /></span>
                    <span className="fig">{fmt(g.actual)}</span>
                  </div>
                )
              })}
            </div>
            <div className="ci">
              <span className="c"># Fail CI when this agent misses its gate</span>{'\n'}
              <span className="k">run:</span> aether gate --agent {agentRef}{modelRef ? ` -m ${modelRef}` : ''} --trials {r.config.trials}{'\n'}
              <span className="k">env:</span> AETHER_URL, AETHER_API_KEY
            </div>
          </div>
        </div>
      )}

      {open && <TrialDrawer trialId={open.trial} onClose={() => setOpen(null)} variantLabel={cur ? label(cur, many) : undefined} />}
    </div>
  )
}
