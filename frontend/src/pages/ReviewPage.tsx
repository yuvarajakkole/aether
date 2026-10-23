import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { TrialDrawer } from '../components/TrialDrawer'
import { Empty, useToast } from '../components/ui'
import { api, type ReviewItem } from '../lib/api'
import { CHECK_LABEL, catLabel, pct } from '../lib/format'

export function ReviewPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const items = useQuery({ queryKey: ['review'], queryFn: () => api.get<ReviewItem[]>('/api/v1/review') })
  const stats = useQuery({ queryKey: ['review-stats'], queryFn: () => api.get<{ reviewed: number; agreement: number | null; pending: number }>('/api/v1/review/stats') })
  const [open, setOpen] = useState<string | null>(null)
  const decide = useMutation({
    mutationFn: ({ id, verdict }: { id: string; verdict: 'pass' | 'fail' }) => api.post(`/api/v1/trials/${id}/review`, { verdict }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['review'] }); qc.invalidateQueries({ queryKey: ['review-stats'] }); toast('Verdict recorded.') },
    onError: (e: Error) => toast(e.message, true),
  })
  const list = items.data ?? []
  return (
    <div className="page">
      <div className="pagehead">
        <div>
          <h1>Review</h1>
          <p>Attempts where the judge was unsure, or disagreed with the rule-based checks. Your verdict overrides the automatic one and tells you how far to trust the judge.</p>
        </div>
      </div>
      <dl className="deltas" style={{ marginTop: 24 }}>
        <div><span className="small muted">Waiting for you</span><span className="fig">{stats.data?.pending ?? '–'}</span></div>
        <div><span className="small muted">Reviewed so far</span><span className="fig">{stats.data?.reviewed ?? '–'}</span></div>
        <div><span className="small muted">You agreed with the judge</span><span className="fig">{pct(stats.data?.agreement)}</span></div>
      </dl>
      <div className="sec">
        <div className="sec-h"><h2>Queue</h2></div>
        {list.length === 0 && !items.isLoading && <Empty title="Nothing to review">When the judge is unsure about an attempt, it shows up here.</Empty>}
        {list.map((t) => {
          const failed = (t.checks ?? []).filter((c) => !c.passed).map((c) => CHECK_LABEL[c.name] ?? c.name)
          return (
            <div className="gt" key={t.id} style={{ gridTemplateColumns: '150px 1fr auto' }}>
              <span className="k">Run {t.run_number}<br />{catLabel(t.category)}</span>
              <div>
                <div className="q">{t.task_input}</div>
                <div className="e">
                  Automatic result: <b>{t.passed ? 'pass' : 'fail'}</b>{failed.length ? ` (${failed.join(', ')})` : ''}. Judge {t.judge?.passed ? 'passed' : 'failed'} it with {pct(t.judge?.confidence)} confidence: {t.judge?.reasoning}
                </div>
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn sm" onClick={() => setOpen(t.id)}>Replay</button>
                  <Link className="link small" to={`/runs/${t.run_id}`}>Open run</Link>
                </div>
              </div>
              <div className="row">
                <button className="btn sm" onClick={() => decide.mutate({ id: t.id, verdict: 'pass' })}>Pass</button>
                <button className="btn sm" onClick={() => decide.mutate({ id: t.id, verdict: 'fail' })}>Fail</button>
              </div>
            </div>
          )
        })}
      </div>
      {open && <TrialDrawer trialId={open} onClose={() => { setOpen(null); qc.invalidateQueries({ queryKey: ['review'] }) }} />}
    </div>
  )
}
