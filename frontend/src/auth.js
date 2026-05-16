/**
 * Token & user storage helpers.
 * JWT is stored in localStorage under 'balloon_token'.
 * User profile (email, role, tenant_id, user_id) is stored as JSON under 'balloon_user'.
 */

const TOKEN_KEY = 'balloon_token'
const USER_KEY  = 'balloon_user'

export function saveSession(tokenResponse) {
  localStorage.setItem(TOKEN_KEY, tokenResponse.access_token)
  localStorage.setItem(USER_KEY, JSON.stringify({
    email:     tokenResponse.email,
    role:      tokenResponse.role,
    tenant_id: tokenResponse.tenant_id,
    user_id:   tokenResponse.user_id,
  }))
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || null
}

export function getUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY)) || null
  } catch {
    return null
  }
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function isLoggedIn() {
  return Boolean(getToken())
}
