import { ref } from 'vue'

export function useWebSocket(getUrl, { onMessage, onOpen, onClose } = {}) {
  const status = ref('disconnected')
  let ws = null
  let retryMs = 1000
  const maxRetryMs = 30000
  let retryTimer = null
  let pingInterval = null

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
      onOpen?.()
    }

    ws.onmessage = (e) => {
      try {
        onMessage?.(JSON.parse(e.data))
      } catch { /* ignore bad messages */ }
    }

    ws.onclose = (evt) => {
      stopPing()
      status.value = 'disconnected'
      onClose?.()
      if (evt.code !== 1000) {
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

  return { status, connect, disconnect, send }
}
