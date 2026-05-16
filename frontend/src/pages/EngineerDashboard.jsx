import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import TopNav from '../components/TopNav.jsx'
import { sessionAPI, authAPI } from '../services/api.js'
import { getUser, getToken } from '../auth.js'

function formatDate(iso) {
  const d = new Date(iso)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

// ── Stats derived from sessions ────────────────────────────────────────────
function Stats({ sessions }) {
  const total   = sessions.length
  const balloons = sessions.reduce((s, sess) => s + (sess.balloon_count || 0), 0)
  const last    = sessions[0]?.created_at

  return (
    <div className="stats-row">
      <div className="stat-card">
        <div className="stat-label">Sessions Saved</div>
        <div className="stat-value">{total}</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Total Balloons</div>
        <div className="stat-value">{balloons}</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Last Session</div>
        <div style={{ fontSize: '0.95rem', fontWeight: 600, marginTop: '0.25rem', color: '#e8eef5' }}>
          {last ? formatDate(last) : '—'}
        </div>
      </div>
    </div>
  )
}

// ── Session card ───────────────────────────────────────────────────────────
function SessionCard({ session, onClick }) {
  return (
    <div
      className="card"
      onClick={onClick}
      style={{ cursor: 'pointer', padding: '1rem', display: 'flex', gap: '1rem', alignItems: 'center', transition: 'border-color 0.15s' }}
      onMouseEnter={e => e.currentTarget.style.borderColor = '#3b82f6'}
      onMouseLeave={e => e.currentTarget.style.borderColor = ''}
    >
      {/* Thumbnail */}
      {session.drawing_preview_b64 ? (
        <img
          src={session.drawing_preview_b64}
          alt="preview"
          style={{ width: 80, height: 60, objectFit: 'cover', borderRadius: '6px', border: '1px solid #2d3a4d', flexShrink: 0 }}
        />
      ) : (
        <div style={{ width: 80, height: 60, borderRadius: '6px', background: '#1a2333', border: '1px solid #2d3a4d', flexShrink: 0 }} />
      )}

      {/* Info */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, color: '#e8eef5', marginBottom: '0.2rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {session.filename}
        </div>
        <div style={{ fontSize: '0.82rem', color: '#8899aa' }}>
          {formatDate(session.created_at)}
        </div>
      </div>

      {/* Balloon count badge */}
      <div style={{ textAlign: 'right', flexShrink: 0 }}>
        <span className="badge badge-blue">{session.balloon_count} balloon{session.balloon_count !== 1 ? 's' : ''}</span>
      </div>
    </div>
  )
}

// ── Trial status banner ────────────────────────────────────────────────────
function TrialBanner({ trialInfo, onUpgrade }) {
  if (!trialInfo || trialInfo.subscription_status === 'active') return null

  const { subscription_status, days_remaining } = trialInfo

  if (subscription_status === 'expired') {
    return (
      <div style={{
        background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.35)',
        borderRadius: '10px', padding: '0.875rem 1.25rem', marginBottom: '1.25rem',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem',
      }}>
        <span style={{ color: '#fca5a5', fontWeight: 600 }}>
          Your 7-day free trial has expired. Upgrade to continue using SmorX.ai.
        </span>
        <button
          className="btn btn-primary btn-sm"
          onClick={onUpgrade}
          style={{ flexShrink: 0, background: '#dc2626', borderColor: '#dc2626' }}
        >
          Upgrade Now
        </button>
      </div>
    )
  }

  if (subscription_status === 'trial') {
    const urgent = days_remaining <= 1
    return (
      <div style={{
        background: urgent ? 'rgba(245,158,11,0.12)' : 'rgba(59,130,246,0.10)',
        border: `1px solid ${urgent ? 'rgba(245,158,11,0.35)' : 'rgba(59,130,246,0.25)'}`,
        borderRadius: '10px', padding: '0.875rem 1.25rem', marginBottom: '1.25rem',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem',
      }}>
        <span style={{ color: urgent ? '#fcd34d' : '#93c5fd', fontWeight: 500 }}>
          {urgent
            ? `Trial expires today! Upgrade to keep access.`
            : `Free trial active · ${days_remaining} day${days_remaining !== 1 ? 's' : ''} remaining.`}
        </span>
        <button className="btn btn-primary btn-sm" onClick={onUpgrade} style={{ flexShrink: 0 }}>
          Upgrade
        </button>
      </div>
    )
  }

  return null
}

// ── Page ───────────────────────────────────────────────────────────────────
export default function EngineerDashboard() {
  const user = getUser()
  const navigate = useNavigate()
  const [sessions,   setSessions]   = useState([])
  const [loading,    setLoading]    = useState(true)
  const [err,        setErr]        = useState('')
  const [trialInfo,  setTrialInfo]  = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    const [{ data, error }, { data: trial }] = await Promise.all([
      sessionAPI.list(),
      authAPI.trialStatus(),
    ])
    setLoading(false)
    if (error) return setErr(error)
    setSessions(data || [])
    if (trial) setTrialInfo(trial)
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="shell">
      <TopNav title="Engineer Dashboard" />

      <div className="container" style={{ padding: '1.5rem' }}>

        {/* Welcome row */}
        <div style={{ marginBottom: '1.5rem' }}>
          <h1 style={{ marginBottom: '0.25rem' }}>Welcome back</h1>
          <p style={{ margin: 0 }}>
            Tenant: <span className="badge badge-blue">{user?.tenant_id || '—'}</span>
            &nbsp;&nbsp;·&nbsp;&nbsp;
            All sessions are scoped to your organization.
          </p>
        </div>

        {/* Trial / subscription banner */}
        <TrialBanner trialInfo={trialInfo} onUpgrade={() => navigate('/payment')} />

        {err && <div className="alert alert-error">{err}</div>}

        {/* Stats */}
        <Stats sessions={sessions} />

        {/* Sessions list */}
        <div className="card">
          <div className="flex-between mb-2">
            <h2>Saved Sessions</h2>
            <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading}>
              ↻ Refresh
            </button>
          </div>

          {loading ? (
            <div className="spinner-center"><div className="spinner" /></div>
          ) : sessions.length === 0 ? (
            <div className="empty">
              No sessions yet.<br />
              <span style={{ fontSize: '0.82rem' }}>
                Upload a drawing, run auto ballooning, then click "Go to Dashboard" to save.
              </span>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {sessions.map(s => (
                <SessionCard
                  key={s.id}
                  session={s}
                  onClick={() => navigate(`/activities/${s.id}`)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Link to main app — passes JWT so the app recognises the logged-in user */}
        <div className="card mt-2" style={{ textAlign: 'center', padding: '1.25rem' }}>
          <p style={{ marginBottom: '0.75rem' }}>Ready to process a drawing?</p>
          <button
            className="btn btn-primary"
            onClick={() => {
              const token = getToken()
              const url = `http://127.0.0.1:10000/app${token ? `?token=${encodeURIComponent(token)}` : ''}`
              window.location.href = url
            }}
          >
            Open Auto Ballooning App →
          </button>
        </div>
      </div>
    </div>
  )
}
