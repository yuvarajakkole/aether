import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type Trial, type TraceStep } from '../lib/api'
import { CHECK_LABEL, catLabel, money, num, secs } from '../lib/format'
import { useToast } from './ui'

function json(v: unknown) {
  if (typeof v === 'string') return v
  return JSON.stringify(v, null, 1)
}

function Step({ s }: { s: TraceStep }) {
  const t = s.t_ms !== undefined ? secs(s.t_ms) : ''
  if (s.type === 'user')
    return (
      <div className="ev">
        <div className="who">
          {s.simulated ? 'Simulated user' : 'User'}
          <span className="t">{t}</span>
        </div>
        <div className="body">{s.content}</div>
      </div>
    )
  if (s.type === 'assistant')
    return (
      <div className="ev">
        <div className="who">
          Agent{s.interim ? ', thinking' : ''}
          <span className="t">{t}</span>
        </div>
        <div className="body">{s.content || <span className="muted">(empty reply)</span>}</div>
      </div>
    )
  if (s.type === 'tool_call') {
    const args = Object.entries(s.arguments ?? {})
      .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
      .join(', ')
    return (
      <div className={`ev ${s.parse_error ? 'err' : ''}`}>
        <div className="who">
          Tool call<span className="t">{s.latency_ms !== undefined ? secs(s.latency_ms) : t}</span>
        </div>
        <div className="body">
          <div className="call">
            {s.name}({args})
          </div>
          {s.parse_error && <div className="small" style={{ color: 'var(--fail)', marginTop: 6 }}>{s.parse_error}</div>}
        </div>
      </div>
    )
  }
  if (s.type === 'tool_result') {
    const bad = s.injected || !!s.invalid_args
    return (
      <div className={`ev ${bad ? 'err' : ''}`}>
        <div className="who">
          {s.external ? 'Tool result' : s.simulated === false ? 'Real API' : 'Simulated API'}
          <span className="t">{s.latency_ms !== undefined ? secs(s.latency_ms) : ''}</span>
        </div>
        <div className="body">
          <div className="call">{json(s.result)}</div>
          <div className="tagline">
            {s.injected && <span className="tag fail">Failure injected by Aether</span>}
            {s.invalid_args && <span className="tag fail">Rejected: invalid arguments</span>}
          </div>
        </div>
      </div>
    )
  }
  return (
    <div className="ev err">
      <div className="who">Error</div>
      <div className="body">{s.content}</div>
    </div>
  )
}

export function TrialDrawer({ trialId, onClose, variantLabel }: { trialId: string; onClose: () => void; variantLabel?: string }) {
  const [current, setCurrent] = useState(trialId)
  useEffect(() => setCurrent(trialId), [trialId])
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({ queryKey: ['trial', current], queryFn: () => api.get<Trial>(`/api/v1/trials/${current}`) })
  const review = useMutation({
    mutationFn: (verdict: 'pass' | 'fail' | null) => api.post<Trial>(`/api/v1/trials/${current}/review`, { verdict }),
    onSuccess: (t) => {
      qc.setQueryData(['trial', current], (old: Trial | undefined) => ({ ...old, ...t }))
      qc.invalidateQueries({ queryKey: ['review'] })
      qc.invalidateQueries({ queryKey: ['review-stats'] })
      toast(t.human_verdict ? `Marked as ${t.human_verdict === 'pass' ? 'passed' : 'failed'}. Re-analyse the run to update metrics.` : 'Human verdict cleared.')
    },
    onError: (e: Error) => toast(e.message, true),
  })
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    addEventListener('keydown', k)
    return () => removeEventListener('keydown', k)
  }, [onClose])
  const t = q.data
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="insp" aria-label="Attempt replay">
        <div className="insp-h">
          <div className="row small muted">
            <span>{t?.task ? catLabel(t.task.category) : ''}</span>
            <span>{t?.task?.ref}</span>
            {variantLabel && <span>{variantLabel}</span>}
            <button className="x" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
          <h3>{t?.task?.input ?? 'Loading…'}</h3>
          {t?.attempts && (
            <div className="attempts">
              {t.attempts.map((a) => (
                <button
                  key={a.id}
                  className={`${a.passed === true ? 'pass' : a.passed === false ? 'fail' : ''} ${a.id === current ? 'on' : ''}`}
                  onClick={() => setCurrent(a.id)}
                  aria-label={`Attempt ${a.index + 1}: ${a.passed ? 'passed' : a.passed === false ? 'failed' : a.status}`}
                >
                  {a.index + 1}
                </button>
              ))}
              <span className="small muted">
                passed {t.attempts.filter((a) => a.passed).length} of {t.attempts.length}
              </span>
            </div>
          )}
        </div>
        <div className="insp-b">
          {q.isLoading && <div className="loading">Loading attempt…</div>}
          {q.error && <div className="error-box">{(q.error as Error).message}</div>}
          {t && (
            <>
              {t.task?.scenario && (
                <div className="note-box" style={{ marginTop: 12 }}>
                  <b>Hidden scenario for the simulated user.</b> {t.task.scenario}
                </div>
              )}
              {(t.trace ?? []).filter((s) => s.type !== 'summary').map((s, i) => (
                <Step key={i} s={s} />
              ))}
              {t.status === 'queued' || t.status === 'running' ? <div className="loading">This attempt is still {t.status}.</div> : null}
              <div className="scorelist">
                {(t.checks ?? []).map((c) => (
                  <div className="sc" key={c.name}>
                    <div>
                      {CHECK_LABEL[c.name] ?? c.name}
                      {c.detail && !c.passed && <span className="d">{c.detail}</span>}
                    </div>
                    <span className={`r ${!c.gating ? 'n' : c.passed ? '' : 'f'}`}>{!c.gating ? 'Skipped' : c.passed ? 'Pass' : 'Fail'}</span>
                  </div>
                ))}
              </div>
              {t.judge?.reasoning && (
                <div className="judge">
                  <b>Judge{t.judge.model ? ` (${t.judge.model})` : ''}.</b> {t.judge.reasoning}
                  {!!t.judge.policy_violations?.length && <div className="small" style={{ marginTop: 6 }}>Policy: {t.judge.policy_violations.join('; ')}</div>}
                  {!!t.judge.unsupported_claims?.length && <div className="small" style={{ marginTop: 4 }}>Unsupported: {t.judge.unsupported_claims.join('; ')}</div>}
                  {t.judge.confidence !== undefined && <div className="small muted" style={{ marginTop: 4 }}>Confidence {Math.round(t.judge.confidence * 100)}%</div>}
                </div>
              )}
              {t.human_verdict && (
                <div className="note-box" style={{ marginTop: 12 }}>
                  Human verdict: <b>{t.human_verdict === 'pass' ? 'Pass' : 'Fail'}</b>
                  {t.reviewed_by ? ` by ${t.reviewed_by}` : ''}. It overrides the automatic result in metrics.
                </div>
              )}
              <p className="small muted" style={{ marginTop: 14 }}>
                Agent latency {secs(t.latency_ms)}
                {(() => { const w = t.trace?.find((s) => s.type === 'summary')?.wall_ms; return w ? ` (${secs(w)} including simulation and judging)` : '' })()}
                , {num(t.input_tokens)} in / {num(t.output_tokens)} out tokens, agent {money(t.cost_usd)}, evaluation {money(t.eval_cost_usd)}
                {t.error ? `. ${t.error}` : ''}
              </p>
            </>
          )}
        </div>
        <div className="insp-f">
          <button className="btn" disabled={review.isPending} onClick={() => review.mutate('pass')}>
            This should pass
          </button>
          <button className="btn" disabled={review.isPending} onClick={() => review.mutate('fail')}>
            This should fail
          </button>
          {t?.human_verdict && (
            <button className="btn ghost" onClick={() => review.mutate(null)}>
              Clear verdict
            </button>
          )}
        </div>
      </aside>
    </>
  )
}
