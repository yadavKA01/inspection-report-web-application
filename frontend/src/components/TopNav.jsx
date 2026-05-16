import { useNavigate } from 'react-router-dom'
import { clearSession, getUser } from '../auth.js'
import smorxLogo from '../assets/smorx-logo.jpg'

export default function TopNav({ title }) {
  const navigate = useNavigate()
  const user = getUser()

  function logout() {
    clearSession()
    navigate('/login')
  }

  return (
    <nav className="topnav">
      <div className="topnav-brand">
        <img
          src={smorxLogo}
          alt="SmorX.ai"
          style={{ height: '40px', objectFit: 'contain', borderRadius: '4px' }}
        />
        {title && <span style={{ color: '#8b9cb3', fontWeight: 400 }}>{title}</span>}
      </div>

      <div className="topnav-right">
        {user && (
          <span className="topnav-user">
            <span
              className={`badge ${user.role === 'super_admin' ? 'badge-yellow' : 'badge-blue'}`}
              style={{ marginRight: '0.5rem' }}
            >
              {user.role === 'super_admin' ? 'Super Admin' : 'Engineer'}
            </span>
            {user.email}
          </span>
        )}
        <button className="btn btn-ghost btn-sm" onClick={logout}>
          Sign out
        </button>
      </div>
    </nav>
  )
}
