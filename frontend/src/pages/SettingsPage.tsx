import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useToast } from '../components/ui'
import { api, auth, type Meta } from '../lib/api'
import { ago } from '../lib/format'

type Key = { id: string; name: string; prefix: string; revoked: boolean; created_at: string; last_used_at: string | null }
type Audit = { id: string; actor: string; action: string; entity_type: string; entity_id: string | null; detail: Record<string, unknown>; created_at: string }

export function SettingsPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.get<Meta>('/api/v1/meta'), staleTime: Infinity })
  const me = useQuery({ queryKey: ['me'], queryFn: () => api.get<{ actor: string; via: string; workspace: { name: string; slug: string } }>('/api/v1/me'), retry: false })
  const keys = useQuery({ queryKey: ['keys'], queryFn: () => api.get<Key[]>('/api/v1/api-keys'), enabled: !me.error })
  const audit = useQuery({ queryKey: ['audit'], queryFn: () => api.get<Audit[]>('/api/v1/audit?limit=60'), enabled: !me.error })
  const [name, setName] = useState('')
  const [token, setToken] = useState<string | null>(null)
  const [browserKey, setBrowserKey] = useState('')
  const create = useMutation({
    mutationFn: () => api.post<{ token: string }>('/api/v1/api-keys', { name }),
    onSuccess: (r) => { setToken(r.token); setName(''); qc.invalidateQueries({ queryKey: ['keys'] }) },
    onError: (e: Error) => toast(e.message, true),
  })
  const revoke = useMutation({
    mutationFn: (id: string) => api.del(`/api/v1/api-keys/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['keys'] }); toast('Key revoked.') },
  })
  return (
    <div className="page">
      <div className="pagehead"><div><h1>Settings</h1><p>Workspace access, API keys for CI, and a record of every change.</p></div></div>
      <div className="split" style={{ marginTop: 30 }}>
        <div>
          <div className="sec-h"><h2>API keys</h2><p>For CI pipelines and scripts. Send as "Authorization: Bearer ak_…".</p></div>
          <div className="row" style={{ marginTop: 14 }}>
            <input className="input" style={{ maxWidth: 320 }} value={name} onChange={(e) => setName(e.target.value)} placeholder="Key name, e.g. github-actions" />
            <button className="btn ink" disabled={!name || create.isPending} onClick={() => create.mutate()}>Create key</button>
          </div>
          {token && <div className="note-box" style={{ marginTop: 12 }}>Copy this key now; it will not be shown again.<div className="code" style={{ marginTop: 6, wordBreak: 'break-all', color: 'var(--ink)' }}>{token}</div></div>}
          <table className="table" style={{ marginTop: 10 }}>
            <tbody>
              {(keys.data ?? []).map((k) => (
                <tr key={k.id} style={{ opacity: k.revoked ? 0.5 : 1 }}>
                  <td><b>{k.name}</b><div className="small muted code">{k.prefix}…</div></td>
                  <td className="small muted">created {ago(k.created_at)}</td>
                  <td className="small muted">{k.last_used_at ? `used ${ago(k.last_used_at)}` : 'never used'}</td>
                  <td>{k.revoked ? <span className="small muted">revoked</span> : <button className="btn ghost sm danger" onClick={() => revoke.mutate(k.id)}>Revoke</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="sec">
            <div className="sec-h"><h2>Audit log</h2><p>Who changed what</p></div>
            <table className="table">
              <tbody>
                {(audit.data ?? []).map((e) => (
                  <tr key={e.id}>
                    <td className="small muted" style={{ whiteSpace: 'nowrap' }}>{ago(e.created_at)}</td>
                    <td className="small">{e.actor}</td>
                    <td className="small">{e.action} {e.entity_type.replace('_', ' ')}</td>
                    <td className="small muted">{Object.entries(e.detail ?? {}).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <aside>
          <div className="sec-h"><h2>Workspace</h2></div>
          <dl className="kv" style={{ marginTop: 14 }}>
            <dt>Organisation</dt><dd>{meta.data?.org_name}</dd>
            <dt>Workspace</dt><dd>{me.data?.workspace.name ?? '–'}</dd>
            <dt>Signed in as</dt><dd>{me.data?.actor ?? 'not signed in'}</dd>
            <dt>Authentication</dt><dd>{{ none: 'Off (local mode)', api_key: 'API keys', oidc: 'Single sign-on (OIDC)' }[meta.data?.auth_mode ?? 'none']}</dd>
            <dt>Version</dt><dd>{meta.data?.version}</dd>
          </dl>
          <div className="sec">
            <div className="sec-h"><h2>This browser</h2></div>
            <p className="small ink2" style={{ marginTop: 10 }}>When authentication is on, paste an API key or access token to use the app from this browser.</p>
            <div className="row" style={{ marginTop: 10 }}>
              <input className="input" type="password" value={browserKey} onChange={(e) => setBrowserKey(e.target.value)} placeholder={auth.get() ? 'A key is saved' : 'ak_…'} />
              <button className="btn" onClick={() => { auth.set(browserKey || null); setBrowserKey(''); qc.invalidateQueries(); toast(browserKey ? 'Key saved for this browser.' : 'Key removed.') }}>{browserKey ? 'Save' : 'Clear'}</button>
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
