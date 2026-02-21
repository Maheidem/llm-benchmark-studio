import { ref } from 'vue'

export function useWebSocket(getUrl, { onMessage, onOpen, onClose } = {}) {
  const status = ref('disconnected')
  let ws = null
  let retryMs = 1000
  const maxRetryMs = 30000
  let retryTimer = null
  let pingInterval = null
  let refreshTimer = null
  const REFRESH_INTERVAL_MS = 12 * 60 * 1000 // 12 minutes — well before 15-min token expiry

  function connect() {
    if (ws && ws.readyState <= 1) return
    status.value = 'connecting'
    const url = typeof getUrl === 'function' ? getUrl() : getUrl

    try {
      ws = new WebSocket(url)
    } catch (e) {
      console.warn('[WS] Failed to create WebSocket:', e)
      status.value = 'disconnected'
      scheduleRetry()
      return
    }

    ws.onopen = () => {
      status.value = 'connected'
      retryMs = 1000
      startPing()
      startTokenRefresh()
      onOpen?.()
    }

    ws.onmessage = (e) => {
      try {
        onMessage?.(JSON.parse(e.data))
      } catch { /* ignore bad messages */ }
    }

    ws.onclose = (evt) => {
      stopPing()
      stopTokenRefresh()
      status.value = 'disconnected'
      onClose?.()
      if (evt.code === 4001 || evt.code === 4003) {
        // Auth failure — try refreshing token before retry
        handleAuthFailure()
      } else if (evt.code !== 1000) {
        scheduleRetry()
      }
    }

    ws.onerror = () => {
      status.value = 'disconnected'
    }
  }

  function disconnect() {
    clearTimeout(retryTimer)
    retryTimer = null
    stopPing()
    stopTokenRefresh()
    if (ws) {
      ws.close(1000)
      ws = null
    }
    status.value = 'disconnected'
  }

  function send(data) {
    if (ws?.readyState === 1) ws.send(JSON.stringify(data))
  }

  function scheduleRetry() {
    if (retryTimer) clearTimeout(retryTimer)
    const jitter = Math.random() * 1000
    retryTimer = setTimeout(() => {
      retryTimer = null
      connect()
    }, retryMs + jitter)
    retryMs = Math.min(retryMs * 2, maxRetryMs)
  }

  function startPing() {
    stopPing()
    pingInterval = setInterval(() => {
      send({ type: 'ping' })
    }, 30000)
  }

  function stopPing() {
    if (pingInterval) {
      clearInterval(pingInterval)
      pingInterval = null
    }
  }

  function startTokenRefresh() {
    stopTokenRefresh()
    refreshTimer = setInterval(async () => {
      try {
        const refresh = localStorage.getItem('refresh_token')
        if (!refresh) return

        const res = await fetch('/api/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refresh }),
        })
        if (res.ok) {
          const data = await res.json()
          if (data.access_token) {
            localStorage.setItem('auth_token', data.access_token)
            if (data.refresh_token) {
              localStorage.setItem('refresh_token', data.refresh_token)
            }
            // Reconnect WS with fresh token
            if (ws && ws.readyState === 1) {
              ws.close(1000)
              // code=1000 won't trigger scheduleRetry, so reconnect manually
              setTimeout(() => connect(), 500)
            }
          }
        }
      } catch {
        // Refresh failed — keep existing connection alive
      }
    }, REFRESH_INTERVAL_MS)
  }

  function stopTokenRefresh() {
    if (refreshTimer) {
      clearInterval(refreshTimer)
      refreshTimer = null
    }
  }

  async function handleAuthFailure() {
    try {
      const refresh = localStorage.getItem('refresh_token')
      if (!refresh) {
        scheduleRetry()
        return
      }

      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      if (res.ok) {
        const data = await res.json()
        if (data.access_token) {
          localStorage.setItem('auth_token', data.access_token)
          if (data.refresh_token) {
            localStorage.setItem('refresh_token', data.refresh_token)
          }
          setTimeout(() => connect(), 500)
          return
        }
      }
    } catch { /* ignore */ }
    // If refresh failed, do normal retry (which will eventually fail with bad token)
    scheduleRetry()
  }

  return { status, connect, disconnect, send }
}
