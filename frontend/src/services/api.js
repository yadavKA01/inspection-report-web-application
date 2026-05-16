/**
 * Central API service.
 * Every function returns { data, error } — never throws.
 * 401 responses clear the session and redirect to /login automatically.
 */
import { getToken, clearSession } from '../auth.js'

const BASE = ''  // Vite proxy handles /auth, /admin, /api → backend

async function request(method, path, body, isFormData = false) {
  const token = getToken()
  const headers = {}

  if (token) headers['Authorization'] = `Bearer ${token}`
  if (!isFormData) headers['Content-Type'] = 'application/json'

  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: isFormData ? body : (body ? JSON.stringify(body) : undefined),
    })

    // Auto-logout on 401
    if (res.status === 401) {
      clearSession()
      window.location.href = '/login'
      return { data: null, error: 'Session expired. Please log in again.' }
    }

    let data
    const text = await res.text()
    try { data = JSON.parse(text) } catch { data = { detail: text } }

    // Trial expired — redirect to payment page
    if (res.status === 403 && data?.detail?.error === 'TRIAL_EXPIRED') {
      window.location.href = '/payment'
      return { data: null, error: 'TRIAL_EXPIRED' }
    }

    if (!res.ok) {
      const detail = data?.detail
      let msg
      if (typeof detail === 'string')      msg = detail
      else if (Array.isArray(detail))      msg = detail.map(d => d.msg || JSON.stringify(d)).join(' ')
      else if (detail)                     msg = JSON.stringify(detail)
      else                                 msg = data?.message || `Error ${res.status}`
      return { data: null, error: msg }
    }

    return { data, error: null }
  } catch (e) {
    return { data: null, error: 'Network error — is the backend running?' }
  }
}

const get  = (path)        => request('GET',    path)
const post = (path, body)  => request('POST',   path, body)
const del  = (path)        => request('DELETE', path)

// ── Auth ──────────────────────────────────────────────────────────────────
export const authAPI = {
  login:          (email, password)       => post('/auth/login', { email, password }),
  changePassword: (newPwd, confirmPwd)    => post('/auth/change-password', { new_password: newPwd, confirm_password: confirmPwd }),
  forgotPassword: (email)                 => post('/auth/forgot-password', { email }),
  me:             ()                      => get('/auth/me'),
  trialStatus:    ()                      => get('/api/v1/trial-status'),
}

// ── Super Admin — Organizations ──────────────────────────────────────────
export const orgAPI = {
  list:   ()     => get('/admin/organizations'),
  create: (name) => post('/admin/organizations', { name }),
}

// ── Super Admin — Engineers ───────────────────────────────────────────────
export const engineerAPI = {
  listAll:       ()          => get('/admin/engineers'),
  listByTenant:  (tenantId)  => get(`/admin/engineers/${tenantId}`),
  create:        (body)      => post('/admin/engineers', body),  // { name, email, tenant_id }
  delete:        (id)        => del(`/admin/engineers/${id}`),
}

// ── Activities (tenant-scoped) ─────────────────────────────────────────────
export const activityAPI = {
  list:          (tenantId) => get(`/api/v1/activities${tenantId ? `?tenant_id=${tenantId}` : ''}`),
}

// ── Drawing Sessions ───────────────────────────────────────────────────────
export const sessionAPI = {
  list:  ()    => get('/activities'),
  get:   (id)  => get(`/activities/${id}`),
  save:  (body) => post('/activities/save', body),
}

// ── Detection (multipart upload) ──────────────────────────────────────────
export const detectAPI = {
  upload: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('POST', '/api/v1/detect', form, true)
  },
}

// ── Payment (Razorpay) ────────────────────────────────────────────────────
export const paymentAPI = {
  createOrder: ()     => post('/payment/create-order', {}),
  verify:      (body) => post('/payment/verify', body),
}
