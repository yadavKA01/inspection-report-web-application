import { useState, useEffect, useCallback } from 'react'
import TopNav from '../components/TopNav.jsx'
import { orgAPI, engineerAPI } from '../services/api.js'

// ── Small helpers ──────────────────────────────────────────────────────────
function Alert({ msg, type = 'error' }) {
  if (!msg) return null
  return <div className={`alert alert-${type}`}>{msg}</div>
}

function Spinner() {
  return <div className="spinner-center"><div className="spinner" /></div>
}

// ── Temp-password modal (shown after creating an engineer) ─────────────────
function TempPasswordModal({ data, onClose }) {
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Engineer created</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="alert alert-info" style={{ marginBottom: '1rem' }}>
          <strong>Important:</strong> Share this temporary password with the engineer.
          Once you close this dialog it cannot be retrieved.
        </div>

        <div className="field">
          <label>Email</label>
          <input readOnly value={data.email} style={{ cursor: 'text' }} />
        </div>
        <div className="field">
          <label>Temporary password</label>
          <input readOnly value={data.temp_password} style={{ cursor: 'text', fontFamily: 'monospace', letterSpacing: '0.1em' }} />
        </div>
        <div className="field">
          <label>Organization (tenant_id)</label>
          <input readOnly value={data.tenant_id} style={{ cursor: 'text', fontFamily: 'monospace' }} />
        </div>

        <p className="mt-1 text-sm text-muted">{data.message}</p>

        <button className="btn btn-primary btn-full mt-3" onClick={onClose}>
          Done — I've noted the password
        </button>
      </div>
    </div>
  )
}

// ── Organizations tab ──────────────────────────────────────────────────────
function OrgsTab() {
  const [orgs, setOrgs]         = useState([])
  const [loading, setLoading]   = useState(true)
  const [name, setName]         = useState('')
  const [creating, setCreating] = useState(false)
  const [err, setErr]           = useState('')
  const [success, setSuccess]   = useState('')

  const fetchOrgs = useCallback(async () => {
    setLoading(true)
    const { data } = await orgAPI.list()
    setOrgs(data || [])
    setLoading(false)
  }, [])

  useEffect(() => { fetchOrgs() }, [fetchOrgs])

  async function handleCreate(e) {
    e.preventDefault()
    setErr(''); setSuccess('')
    if (!name.trim()) return setErr('Organization name is required.')

    setCreating(true)
    const { data, error } = await orgAPI.create(name.trim())
    setCreating(false)

    if (error) return setErr(error)
    setSuccess(`Organization "${data.name}" created (tenant_id: ${data.tenant_id})`)
    setName('')
    fetchOrgs()
  }

  return (
    <div>
      {/* Create form */}
      <div className="card mb-2">
        <h3 style={{ marginBottom: '1rem' }}>Create Organization</h3>
        <Alert msg={err} />
        <Alert msg={success} type="success" />
        <form onSubmit={handleCreate} style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-end' }}>
          <div className="field" style={{ flex: 1, marginBottom: 0 }}>
            <label>Organization Name</label>
            <input
              type="text"
              placeholder="e.g. Acme Engineering"
              value={name}
              onChange={e => setName(e.target.value)}
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={creating}>
            {creating ? 'Creating…' : '+ Create'}
          </button>
        </form>
      </div>

      {/* List */}
      <div className="card">
        <h3 style={{ marginBottom: '1rem' }}>All Organizations ({orgs.length})</h3>
        {loading ? <Spinner /> : orgs.length === 0 ? (
          <div className="empty">No organizations yet. Create one above.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Tenant ID</th>
                  <th>Engineers</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {orgs.map(o => (
                  <tr key={o.id}>
                    <td><strong>{o.name}</strong></td>
                    <td><span className="td-mono">{o.tenant_id}</span></td>
                    <td>
                      <span className="badge badge-blue">{o.engineer_count}</span>
                    </td>
                    <td className="td-mono">{new Date(o.created_at).toLocaleDateString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Engineers tab ──────────────────────────────────────────────────────────
function EngineersTab() {
  const [orgs, setOrgs]           = useState([])
  const [engineers, setEngineers] = useState([])
  const [loading, setLoading]     = useState(true)
  const [filterTenant, setFilter] = useState('')

  // Form state
  const [name, setName]           = useState('')
  const [email, setEmail]         = useState('')
  const [tenantId, setTenantId]   = useState('')
  const [creating, setCreating]   = useState(false)
  const [err, setErr]             = useState('')
  const [createdData, setCreated] = useState(null)  // shown in modal

  const fetchAll = useCallback(async () => {
    setLoading(true)
    const [orgsRes, engRes] = await Promise.all([orgAPI.list(), engineerAPI.listAll()])
    setOrgs(orgsRes.data || [])
    setEngineers(engRes.data || [])
    setLoading(false)
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  async function handleCreate(e) {
    e.preventDefault()
    setErr('')
    if (!name.trim())    return setErr('Name is required.')
    if (!email.trim())   return setErr('Email is required.')
    if (!tenantId)       return setErr('Please select an organization.')

    setCreating(true)
    const { data, error } = await engineerAPI.create({ name: name.trim(), email: email.trim(), tenant_id: tenantId })
    setCreating(false)

    if (error) return setErr(error)
    setCreated(data)   // open modal
    setName(''); setEmail(''); setTenantId('')
    fetchAll()
  }

  async function handleDelete(id, emailAddr) {
    if (!confirm(`Delete engineer ${emailAddr}?`)) return
    const { error } = await engineerAPI.delete(id)
    if (error) alert(error)
    else fetchAll()
  }

  const displayed = filterTenant
    ? engineers.filter(e => e.tenant_id === filterTenant)
    : engineers

  const orgMap = Object.fromEntries(orgs.map(o => [o.tenant_id, o.name]))

  return (
    <div>
      {createdData && (
        <TempPasswordModal data={createdData} onClose={() => setCreated(null)} />
      )}

      {/* Create form */}
      <div className="card mb-2">
        <h3 style={{ marginBottom: '1rem' }}>Create Engineer</h3>
        <Alert msg={err} />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.75rem' }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Full Name</label>
            <input type="text" placeholder="Jane Doe" value={name} onChange={e => setName(e.target.value)} />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Email</label>
            <input type="email" placeholder="jane@company.com" value={email} onChange={e => setEmail(e.target.value)} />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Organization</label>
            <select value={tenantId} onChange={e => setTenantId(e.target.value)}>
              <option value="">Select organization…</option>
              {orgs.map(o => (
                <option key={o.tenant_id} value={o.tenant_id}>{o.name}</option>
              ))}
            </select>
          </div>
        </div>
        <div style={{ marginTop: '0.75rem' }}>
          <button className="btn btn-primary" onClick={handleCreate} disabled={creating}>
            {creating ? 'Creating…' : '+ Create Engineer'}
          </button>
        </div>
      </div>

      {/* List */}
      <div className="card">
        <div className="flex-between mb-2">
          <h3>All Engineers ({displayed.length})</h3>
          <select
            value={filterTenant}
            onChange={e => setFilter(e.target.value)}
            style={{ padding: '0.35rem 0.65rem', background: '#0f1419', border: '1px solid #2d3a4d', borderRadius: '8px', color: '#e8eef5', fontSize: '0.85rem' }}
          >
            <option value="">All organizations</option>
            {orgs.map(o => (
              <option key={o.tenant_id} value={o.tenant_id}>{o.name}</option>
            ))}
          </select>
        </div>

        {loading ? <Spinner /> : displayed.length === 0 ? (
          <div className="empty">No engineers found.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Organization</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {displayed.map(eng => (
                  <tr key={eng.id}>
                    <td><strong>{eng.name}</strong></td>
                    <td>{eng.email}</td>
                    <td>
                      <span className="badge badge-blue">{orgMap[eng.tenant_id] || eng.tenant_id}</span>
                    </td>
                    <td>
                      {eng.is_temp_password
                        ? <span className="badge badge-yellow">Temp password</span>
                        : <span className="badge badge-green">Active</span>
                      }
                    </td>
                    <td className="td-mono">{new Date(eng.created_at).toLocaleDateString()}</td>
                    <td>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => handleDelete(eng.id, eng.email)}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────
export default function SuperAdminDashboard() {
  const [tab, setTab] = useState('orgs')

  return (
    <div className="shell">
      <TopNav title="Super Admin" />

      <div className="container" style={{ padding: '1.5rem' }}>
        <div className="tabs">
          <button className={`tab ${tab === 'orgs' ? 'active' : ''}`}     onClick={() => setTab('orgs')}>
            Organizations
          </button>
          <button className={`tab ${tab === 'engineers' ? 'active' : ''}`} onClick={() => setTab('engineers')}>
            Engineers
          </button>
        </div>

        {tab === 'orgs'      && <OrgsTab />}
        {tab === 'engineers' && <EngineersTab />}
      </div>
    </div>
  )
}
