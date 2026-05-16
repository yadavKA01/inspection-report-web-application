import { Navigate } from 'react-router-dom'
import { getToken, getUser } from '../auth.js'

/**
 * Wraps any route that requires authentication.
 *
 * Props:
 *   role  (optional) — 'super_admin' | 'engineer'
 *                       If provided, also checks that the user's role matches.
 *
 * Behaviour:
 *   - No token          → redirect to /login
 *   - Wrong role        → redirect to the correct dashboard for their role
 *   - Token + right role → render children
 */
export default function ProtectedRoute({ children, role }) {
  const token = getToken()
  const user  = getUser()

  if (!token || !user) {
    return <Navigate to="/login" replace />
  }

  if (role && user.role !== role) {
    const dest = user.role === 'super_admin' ? '/admin' : '/dashboard'
    return <Navigate to={dest} replace />
  }

  return children
}
