import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { authAPI } from '../services/api.js'
import { saveSession, getToken, getUser, clearSession } from '../auth.js'
import smorxLogo from '../assets/smorx-logo.jpg'

// ── Shared tiny components ─────────────────────────────────────────────────
function Alert({ msg, type = 'error' }) {
  if (!msg) return null
  return <div className={`alert alert-${type}`}>{msg}</div>
}

// ── Panels ─────────────────────────────────────────────────────────────────
function LoginPanel({ onForgot }) {
  const navigate = useNavigate()
  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr]           = useState('')
  const [loading, setLoading]   = useState(false)

  async function handleLogin(e) {
    e.preventDefault()
    setErr('')
    if (!email.trim())    return setErr('Email is required.')
    if (!password)        return setErr('Password is required.')

    setLoading(true)
    const { data, error } = await authAPI.login(email.trim(), password)
    setLoading(false)

    if (error) return setErr(error)

    saveSession(data)

    if (data.requires_password_change) {
      navigate('/change-password')
      return
    }
    if (data.role === 'super_admin') navigate('/admin')
    else                             navigate('/dashboard')
  }

  return (
    <form onSubmit={handleLogin}>
      <Alert msg={err} />

      <div className="field">
        <label>Email</label>
        <input
          type="email"
          placeholder="you@company.com"
          value={email}
          onChange={e => setEmail(e.target.value)}
          autoComplete="username"
          autoFocus
        />
      </div>

      <div className="field">
        <label>Password</label>
        <input
          type="password"
          placeholder="••••••••"
          value={password}
          onChange={e => setPassword(e.target.value)}
          autoComplete="current-password"
          onKeyDown={e => e.key === 'Enter' && handleLogin(e)}
        />
      </div>

      <button
        type="submit"
        className="btn btn-primary btn-full mt-2"
        disabled={loading}
      >
        {loading ? 'Signing in…' : 'Sign in'}
      </button>

      <div className="text-center mt-2">
        <button type="button" className="link" onClick={onForgot}>
          Forgot password?
        </button>
      </div>
    </form>
  )
}

function ForgotPanel({ onBack }) {
  const [email, setEmail]     = useState('')
  const [msg, setMsg]         = useState('')
  const [err, setErr]         = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setMsg(''); setErr('')
    if (!email.trim()) return setErr('Email is required.')

    setLoading(true)
    try {
      const res = await fetch('http://127.0.0.1:10000/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim() }),
      })
      const data = await res.json()
      console.log('Forgot password response:', data)
      setMsg('Check your email for your temporary password.')
    } catch (e) {
      console.error('Forgot password error:', e)
      setErr('Could not reach server. Is the backend running?')
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <Alert msg={err} />
      <Alert msg={msg} type="success" />

      <p className="mb-2" style={{ fontSize: '0.88rem' }}>
        Enter your email address and we'll send a temporary password.
      </p>

      <div className="field">
        <label>Email</label>
        <input
          type="email"
          placeholder="you@company.com"
          value={email}
          onChange={e => setEmail(e.target.value)}
          autoFocus
        />
      </div>

      <button
        type="submit"
        className="btn btn-primary btn-full mt-2"
        disabled={loading}
      >
        {loading ? 'Sending…' : 'Send temporary password'}
      </button>

      <div className="text-center mt-2">
        <button type="button" className="link" onClick={onBack}>← Back to sign in</button>
      </div>
    </form>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────
export default function LoginPage() {
  const navigate          = useNavigate()
  const [panel, setPanel] = useState('login') // 'login' | 'forgot'

  // Already logged in → go to dashboard (both token and user profile required)
  useEffect(() => {
    const token = getToken()
    const user = getUser()
    if (token && user) {
      navigate(user.role === 'super_admin' ? '/admin' : '/dashboard', { replace: true })
    } else if (token && !user) {
      clearSession()
    }
  }, [navigate])

  return (
    <div className="page-center">
      <div className="card card-sm">
        <div className="login-brand">
          <img
            src={smorxLogo}
            alt="SmorX.ai Logo"
            style={{ height: '90px', objectFit: 'contain', marginBottom: '0.75rem' }}
          />
          <p>{panel === 'forgot' ? 'Reset your password' : 'Sign in to your account'}</p>
        </div>

        {panel === 'login'
          ? <LoginPanel  onForgot={() => setPanel('forgot')} />
          : <ForgotPanel onBack={()  => setPanel('login')}  />
        }
      </div>
    </div>
  )
}
