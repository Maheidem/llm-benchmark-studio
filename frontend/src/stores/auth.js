import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiFetch, setTokens, clearTokens, getToken } from '../utils/api.js'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(getToken())
  const user = ref(null)

  const isAuthenticated = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin')

  async function login(email, password) {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ email, password }),
    })
    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.error || 'Authentication failed')
    }
    token.value = data.access_token
    user.value = data.user
    setTokens(data.access_token, data.refresh_token)
    localStorage.setItem('user', JSON.stringify(data.user))
  }

  async function register(email, password) {
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ email, password }),
    })
    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.error || 'Registration failed')
    }
    token.value = data.access_token
    user.value = data.user
    setTokens(data.access_token, data.refresh_token)
    localStorage.setItem('user', JSON.stringify(data.user))
  }

  function logout() {
    fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {})
    token.value = null
    user.value = null
    clearTokens()
    localStorage.removeItem('user')
  }

  async function fetchUser() {
    const res = await apiFetch('/api/auth/me')
    if (!res.ok) throw new Error('Failed to fetch user')
    const data = await res.json()
    user.value = data.user
    localStorage.setItem('user', JSON.stringify(data.user))
  }

  async function refreshAccessToken() {
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        credentials: 'include',
      })
      if (!res.ok) return false
      const data = await res.json()
      token.value = data.access_token
      if (data.user) {
        user.value = data.user
        localStorage.setItem('user', JSON.stringify(data.user))
      }
      setTokens(data.access_token, data.refresh_token)
      return true
    } catch {
      return false
    }
  }

  async function init() {
    if (!token.value) {
      // Try to restore user from localStorage as fallback
      const userStr = localStorage.getItem('user')
      if (userStr) {
        try { user.value = JSON.parse(userStr) } catch { /* ignore */ }
      }
      return
    }

    try {
      await fetchUser()
    } catch {
      // Token might be expired, try refresh
      const refreshed = await refreshAccessToken()
      if (refreshed) {
        try {
          await fetchUser()
        } catch {
          logout()
        }
      } else {
        logout()
      }
    }
  }

  return {
    token,
    user,
    isAuthenticated,
    isAdmin,
    login,
    register,
    logout,
    fetchUser,
    refreshAccessToken,
    init,
  }
})
