/**
 * JWT-aware fetch wrapper with automatic token refresh on 401.
 */

const TOKEN_KEY = 'auth_token'
const REFRESH_KEY = 'refresh_token'

let isRefreshing = false
let refreshQueue = []

function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

function setTokens(access, refresh) {
  localStorage.setItem(TOKEN_KEY, access)
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh)
}

function clearTokens() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

async function refreshToken() {
  const refresh = localStorage.getItem(REFRESH_KEY)
  if (!refresh) throw new Error('No refresh token')

  const res = await fetch('/api/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refresh }),
  })

  if (!res.ok) throw new Error('Refresh failed')

  const data = await res.json()
  setTokens(data.access_token, data.refresh_token || refresh)
  return data.access_token
}

/**
 * Fetch wrapper that injects Authorization header and retries once on 401.
 * @param {string} url
 * @param {RequestInit} options
 * @returns {Promise<Response>}
 */
export async function apiFetch(url, options = {}) {
  const token = getToken()
  const headers = { ...options.headers }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  let res = await fetch(url, { ...options, headers })

  if (res.status === 401 && token) {
    // Attempt token refresh
    if (!isRefreshing) {
      isRefreshing = true
      try {
        const newToken = await refreshToken()
        isRefreshing = false
        refreshQueue.forEach(cb => cb(newToken))
        refreshQueue = []

        headers['Authorization'] = `Bearer ${newToken}`
        res = await fetch(url, { ...options, headers })
      } catch {
        isRefreshing = false
        refreshQueue = []
        clearTokens()
        window.location.href = '/login'
        throw new Error('Session expired')
      }
    } else {
      // Wait for the ongoing refresh
      const newToken = await new Promise(resolve => {
        refreshQueue.push(resolve)
      })
      headers['Authorization'] = `Bearer ${newToken}`
      res = await fetch(url, { ...options, headers })
    }
  }

  return res
}

export { getToken, setTokens, clearTokens }
