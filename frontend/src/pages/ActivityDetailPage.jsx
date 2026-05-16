import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import TopNav from '../components/TopNav.jsx'
import { sessionAPI } from '../services/api.js'

function formatDate(iso) {
  const d = new Date(iso)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export default function ActivityDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    async function load() {
      const { data, error } = await sessionAPI.get(id)
      setLoading(false)
      if (error) return setErr(error)
      setSession(data)
    }
    load()
  }, [id])

  async function handleDownloadExcel() {
    if (!session?.excel_data?.length) return

    try {
      const token = localStorage.getItem('balloon_token')
      // Backend /api/v1/export-excel expects { detection: { balloon_items: [...] }, filename }
      const resp = await fetch('/api/v1/export-excel', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          detection: { balloon_items: session.excel_data },
          filename: session.filename || 'drawing',
        }),
      })
      if (!resp.ok) {
        alert('Excel export failed.')
        return
      }
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = (session.filename || 'drawing').replace(/\.[^.]+$/, '') + '_balloons.xlsx'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      alert('Network error during export.')
    }
  }

  if (loading) return (
    <div className="shell">
      <TopNav title="Session Detail" />
      <div className="spinner-center"><div className="spinner" /></div>
    </div>
  )

  if (err) return (
    <div className="shell">
      <TopNav title="Session Detail" />
      <div className="container" style={{ padding: '1.5rem' }}>
        <div className="alert alert-error">{err}</div>
        <button className="btn btn-secondary mt-2" onClick={() => navigate('/dashboard')}>← Back</button>
      </div>
    </div>
  )

  const items = session?.excel_data || []

  return (
    <div className="shell">
      <TopNav title="Session Detail" />

      <div className="container" style={{ padding: '1.5rem' }}>

        {/* Header row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1.5rem', flexWrap: 'wrap' }}>
          <button className="btn btn-secondary btn-sm" onClick={() => navigate('/dashboard')}>← Back</button>
          <div style={{ flex: 1 }}>
            <h1 style={{ margin: 0 }}>{session.filename}</h1>
            <p style={{ margin: '0.25rem 0 0', color: '#8899aa', fontSize: '0.85rem' }}>
              {formatDate(session.created_at)} · {session.balloon_count} balloon{session.balloon_count !== 1 ? 's' : ''}
            </p>
          </div>
          {items.length > 0 && (
            <button className="btn btn-primary" onClick={handleDownloadExcel}>
              Download Excel
            </button>
          )}
        </div>

        {/* Drawing preview */}
        {session.drawing_preview_b64 && (
          <div className="card mb-2" style={{ textAlign: 'center' }}>
            <h3 style={{ marginBottom: '0.75rem' }}>Annotated Drawing</h3>
            <img
              src={session.drawing_preview_b64}
              alt="Annotated drawing"
              style={{ maxWidth: '100%', borderRadius: '8px', border: '1px solid #2d3a4d' }}
            />
          </div>
        )}

        {/* Balloon items table */}
        <div className="card">
          <h3 style={{ marginBottom: '1rem' }}>Balloon Items ({items.length})</h3>
          {items.length === 0 ? (
            <div className="empty">No balloon items recorded.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Balloon No.</th>
                    <th>Class</th>
                    <th>Nominal Value</th>
                    <th>Tolerance</th>
                    <th>Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item, idx) => (
                    <tr key={idx}>
                      <td className="td-mono">{idx + 1}</td>
                      <td><strong>{item.balloon_number ?? '—'}</strong></td>
                      <td>{item.class_name || '—'}</td>
                      <td>{item.nominal_value ?? '—'}</td>
                      <td>{item.tolerance ?? '—'}</td>
                      <td style={{ color: '#8899aa', fontSize: '0.82rem' }}>
                        {item.others || item.detected_text || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
