import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authAPI } from '../services/api.js'
import { getUser } from '../auth.js'

function rule(label, test) { return { label, test } }

const RULES = [
  rule('At least 8 characters',             p => p.length >= 8),
  rule('One uppercase letter (A–Z)',         p => /[A-Z]/.test(p)),
  rule('One lowercase letter (a–z)',         p => /[a-z]/.test(p)),
  rule('One digit (0–9)',                    p => /\d/.test(p)),
  rule('One special character (!@#$%^&*)',   p => /[!@#$%^&*]/.test(p)),
]

export default function ChangePasswordPage() {
  const navigate          = useNavigate()
  const user              = getUser()
  const [pwd, setPwd]     = useState('')
  const [pwd2, setPwd2]   = useState('')
  const [err, setErr]     = useState('')
  const [ok, setOk]       = useState('')
  const [loading, setLoading] = useState(false)

  const ruleStates = RULES.map(r => ({
    ...r,
    status: pwd === '' ? 'neutral' : r.test(pwd) ? 'ok' : 'fail',
  }))
  const allPassed = ruleStates.every(r => r.status === 'ok')

  async function handleSubmit(e) {
    e.preventDefault()
    setErr(''); setOk('')

    if (!allPassed) return setErr('Password does not meet all requirements.')
    if (pwd !== pwd2) return setErr('Passwords do not match.')

    setLoading(true)
    const { data, error } = await authAPI.changePassword(pwd, pwd2)
    setLoading(false)

    if (error) return setErr(error)

    setOk('Password updated! Redirecting…')
    setTimeout(() => {
      navigate(user?.role === 'super_admin' ? '/admin' : '/dashboard')
    }, 1200)
  }

  return (
    <div className="page-center">
      <div className="card card-sm">
        <div className="login-brand">
          <h1>Set a new password</h1>
          <p>Your temporary password must be changed before continuing.</p>
        </div>

        {err && <div className="alert alert-error">{err}</div>}
        {ok  && <div className="alert alert-success">{ok}</div>}

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label>New password</label>
            <input
              type="password"
              placeholder="••••••••"
              value={pwd}
              onChange={e => setPwd(e.target.value)}
              autoFocus
            />
          </div>

          <ul className="pwd-rules">
            {ruleStates.map(r => (
              <li key={r.label} className={r.status === 'neutral' ? '' : r.status}>
                {r.label}
              </li>
            ))}
          </ul>

          <div className="field mt-2">
            <label>Confirm new password</label>
            <input
              type="password"
              placeholder="••••••••"
              value={pwd2}
              onChange={e => setPwd2(e.target.value)}
            />
            {pwd2 && pwd !== pwd2 && (
              <p style={{ color: '#f87171', fontSize: '0.8rem', marginTop: '0.3rem' }}>
                Passwords do not match
              </p>
            )}
          </div>

          <button
            type="submit"
            className="btn btn-primary btn-full mt-2"
            disabled={loading || !allPassed || pwd !== pwd2}
          >
            {loading ? 'Updating…' : 'Update password'}
          </button>
        </form>
      </div>
    </div>
  )
}
