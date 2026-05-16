import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { getToken, getUser } from './auth.js'
import ProtectedRoute       from './components/ProtectedRoute.jsx'
import LoginPage            from './pages/LoginPage.jsx'
import ChangePasswordPage   from './pages/ChangePasswordPage.jsx'
import SuperAdminDashboard  from './pages/SuperAdminDashboard.jsx'
import EngineerDashboard    from './pages/EngineerDashboard.jsx'
import ActivityDetailPage   from './pages/ActivityDetailPage.jsx'
import PaymentPage          from './pages/PaymentPage.jsx'

function RootRedirect() {
  const token = getToken()
  const user  = getUser()
  if (!token || !user) return <Navigate to="/login" replace />
  if (user.role === 'super_admin') return <Navigate to="/admin" replace />
  return <Navigate to="/dashboard" replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public */}
        <Route path="/login" element={<LoginPage />} />

        {/* Force password change (requires token, any role) */}
        <Route
          path="/change-password"
          element={
            <ProtectedRoute>
              <ChangePasswordPage />
            </ProtectedRoute>
          }
        />

        {/* Super admin only */}
        <Route
          path="/admin"
          element={
            <ProtectedRoute role="super_admin">
              <SuperAdminDashboard />
            </ProtectedRoute>
          }
        />

        {/* Engineer only */}
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute role="engineer">
              <EngineerDashboard />
            </ProtectedRoute>
          }
        />

        {/* Session detail */}
        <Route
          path="/activities/:id"
          element={
            <ProtectedRoute role="engineer">
              <ActivityDetailPage />
            </ProtectedRoute>
          }
        />

        {/* Payment / upgrade page (public — accessible even after trial expiry) */}
        <Route path="/payment" element={<PaymentPage />} />

        {/* Root → smart redirect */}
        <Route path="/" element={<RootRedirect />} />

        {/* Catch-all */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
