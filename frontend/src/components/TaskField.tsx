import { useMemo, useState } from 'react'
import type { Matrix } from '../lib/api'
import { catLabel } from '../lib/format'

type Props = { matrix: Matrix; selectedTask?: string | null; onOpen: (taskId: string, trialId: string | null) => void }

export function TaskField({ matrix, selectedTask, onOpen }: Props) {
  const [tip, setTip] = useState<{ x: number; y: number; text: string; sub: string } | null>(null)
  const n = matrix.trials_per_task
  const cols = n <= 3 ? n : Math.min(4, Math.ceil(n / 2))
  const cell = matrix.tasks.length <= 40 ? 12 : matrix.tasks.length <= 150 ? 8 : 6
  const groups = useMemo(() => {
    const by = new Map<string, Matrix['tasks']>()
    for (const t of matrix.tasks) by.set(t.category, [...(by.get(t.category) ?? []), t])
    const order = ['everyday', 'edge_case', 'policy', 'adversarial', 'tool_failure', 'multi_turn']
    return [...by.entries()].sort((a, b) => (order.indexOf(a[0]) + 99) % 99 - (order.indexOf(b[0]) + 99) % 99)
  }, [matrix])
  const passes = (t: Matrix['tasks'][number]) => t.attempts.filter((c) => c?.state === 'pass').length

  return (
    <div>
      {groups.map(([cat, tasks]) => {
        const sorted = [...tasks].sort((a, b) => passes(b) - passes(a))
        const finished = tasks.filter((t) => t.attempts.every((c) => c && (c.state === 'pass' || c.state === 'fail')))
        const all = finished.filter((t) => passes(t) === n).length
        return (
          <div className="frow" key={cat}>
            <div className="flabel">
              <b>{catLabel(cat)}</b>
              <span className="rate fig">
                {finished.length ? `${Math.round((all / finished.length) * 100)}%` : '–'}
                <small>passed all {n}</small>
              </span>
              <span className="cnt">{tasks.length} tasks</span>
            </div>
            <div className="tiles">
              {sorted.map((t) => {
                const firstFail = t.attempts.find((c) => c?.state === 'fail') ?? t.attempts.find((c) => c)
                return (
                  <button
                    key={t.task_id}
                    className={`tile ${selectedTask === t.task_id ? 'sel' : ''}`}
                    style={{ gridTemplateColumns: `repeat(${cols}, ${cell}px)`, gridAutoRows: `${cell}px` }}
                    aria-label={`${t.ref}: ${t.input}. Passed ${passes(t)} of ${n}.`}
                    onClick={() => onOpen(t.task_id, firstFail?.trial_id ?? null)}
                    onMouseEnter={(e) => setTip({ x: e.clientX, y: e.clientY, text: t.input, sub: `${t.ref}, passed ${passes(t)} of ${n}` })}
                    onMouseMove={(e) => setTip((p) => (p ? { ...p, x: e.clientX, y: e.clientY } : p))}
                    onMouseLeave={() => setTip(null)}
                  >
                    {t.attempts.map((c, j) => (
                      <i key={j} className={c?.state ?? ''} />
                    ))}
                  </button>
                )
              })}
            </div>
          </div>
        )
      })}
      {tip && (
        <div className="tip" style={{ left: Math.min(tip.x + 14, innerWidth - 340), top: tip.y + 16 }}>
          {tip.text}
          <small>{tip.sub}</small>
        </div>
      )}
    </div>
  )
}
