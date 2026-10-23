import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet } from 'react-router-dom'
import { api, type Meta, type Model } from '../lib/api'
import { Logo } from './ui'

export function Shell() {
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.get<Meta>('/api/v1/meta'), staleTime: Infinity })
  const me = useQuery({ queryKey: ['me'], queryFn: () => api.get<{ actor: string; workspace: { name: string } }>('/api/v1/me'), retry: false })
  const review = useQuery({ queryKey: ['review-stats'], queryFn: () => api.get<{ pending: number }>('/api/v1/review/stats'), refetchInterval: 30000 })
  const models = useQuery({ queryKey: ['models'], queryFn: () => api.get<Model[]>('/api/v1/models') })
  const initials = (me.data?.actor ?? 'local').replace(/^api-key:/, '').slice(0, 2).toUpperCase()
  const realModels = (models.data ?? []).filter((m) => m.provider_kind !== 'mock' && m.enabled).length
  return (
    <>
      <header className="bar">
        <NavLink to="/" className="word">
          <Logo />
          aether
        </NavLink>
        <span className="ws">{me.data?.workspace.name ?? meta.data?.org_name ?? ''}</span>
        <nav className="tabs">
          <NavLink to="/" end className="tab">
            Agents
          </NavLink>
          <NavLink to="/runs" className="tab">
            Runs
          </NavLink>
          <NavLink to="/review" className="tab">
            Review {review.data?.pending ? <span className="n">{review.data.pending}</span> : null}
          </NavLink>
          <NavLink to="/models" className="tab">
            Models
          </NavLink>
          <NavLink to="/settings" className="tab">
            Settings
          </NavLink>
        </nav>
        <div className="bar-r">
          <span className="small muted">{realModels ? `${realModels} models connected` : 'No live models yet'}</span>
          <div className="me" title={me.data?.actor}>
            {initials}
          </div>
        </div>
      </header>
      {me.error && (
        <div className="page" style={{ paddingBottom: 0 }}>
          <div className="error-box">This workspace needs authentication. Add an API key in Settings, or sign in through your identity provider.</div>
        </div>
      )}
      <Outlet />
    </>
  )
}
