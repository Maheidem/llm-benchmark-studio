import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiFetch, getToken } from '../utils/api.js'
import { useWebSocket } from '../composables/useWebSocket.js'
import { ACTIVE_STATUSES, JOB_TYPE_LABELS } from '../utils/constants.js'

const NOTIF_MAX_RECENT = 10
const FAIL_THRESHOLD = 5
const FAIL_WINDOW_MS = 60000

export const useNotificationsStore = defineStore('notifications', () => {
  // State
  const jobs = ref({})
  const wsStatus = ref('disconnected')
  const dropdownOpen = ref(false)
  const wsBannerVisible = ref(false)

  // Failure tracking
  let failCount = 0
  let failWindowStart = 0

  // Event bus: domain-specific message handlers can subscribe
  const messageListeners = ref([])

  function onMessage(listener) {
    messageListeners.value.push(listener)
    return () => {
      messageListeners.value = messageListeners.value.filter(l => l !== listener)
    }
  }

  // WebSocket
  const { status, connect: wsConnect, disconnect: wsDisconnect, send } = useWebSocket(
    () => {
      const t = getToken()
      if (!t) return null
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      return `${proto}//${location.host}/ws?token=${encodeURIComponent(t)}`
    },
    {
      onMessage: handleMessage,
      onOpen: () => {
        failCount = 0
        wsBannerVisible.value = false
      },
      onClose: () => {
        trackFailure()
      },
    }
  )

  // Sync wsStatus from composable's status
  // We watch by keeping a reference; the composable's status ref is updated internally
  // We expose it via a computed that reads from the composable
  const computedWsStatus = computed(() => status.value)

  // Getters
  const activeJobs = computed(() => {
    const all = Object.values(jobs.value)
    const running = all
      .filter(j => j.status === 'running')
      .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''))
    const queued = all
      .filter(j => j.status === 'pending' || j.status === 'queued')
      .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''))
    return [...running, ...queued]
  })

  const recentJobs = computed(() => {
    return Object.values(jobs.value)
      .filter(j => !ACTIVE_STATUSES.has(j.status))
      .sort((a, b) => (b.completed_at || b.created_at || '').localeCompare(a.completed_at || a.created_at || ''))
      .slice(0, NOTIF_MAX_RECENT)
  })

  const activeCount = computed(() => activeJobs.value.length)

  // Actions
  function connect() {
    wsConnect()
    hydrate()
  }

  function disconnect() {
    wsDisconnect()
    jobs.value = {}
    failCount = 0
    wsBannerVisible.value = false
  }

  async function hydrate() {
    try {
      const res = await apiFetch('/api/jobs')
      if (!res.ok) return
      const data = await res.json()
      const jobList = Array.isArray(data) ? data : data.jobs || []
      for (const job of jobList) {
        jobs.value[job.id] = job
      }
    } catch (e) {
      console.warn('[Notif] Hydration failed:', e)
    }
  }

  async function cancelJob(jobId) {
    try {
      const res = await apiFetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' })
      if (res.ok) {
        const body = await res.json().catch(() => ({}))
        const wasOrphan = body.was_orphan === true
        if (jobs.value[jobId]) {
          jobs.value[jobId] = {
            ...jobs.value[jobId],
            status: wasOrphan ? 'interrupted' : 'cancelled',
            completed_at: jobs.value[jobId].completed_at || new Date().toISOString(),
          }
        }
        return { success: true, wasOrphan }
      } else if (res.status === 400) {
        // Ghost job: already finished
        if (jobs.value[jobId]) {
          jobs.value[jobId] = {
            ...jobs.value[jobId],
            status: jobs.value[jobId].status === 'running' ? 'interrupted' : jobs.value[jobId].status,
            completed_at: jobs.value[jobId].completed_at || new Date().toISOString(),
          }
        }
        return { success: true, wasOrphan: true }
      } else {
        const err = await res.json().catch(() => ({}))
        return { success: false, error: err.error || 'Failed to cancel' }
      }
    } catch (e) {
      return { success: false, error: 'Cancel failed: ' + e.message }
    }
  }

  function handleMessage(msg) {
    // Handle sync message (sent on connect with full job state)
    if (msg.type === 'sync') {
      const newJobs = {}
      for (const job of (msg.active_jobs || [])) {
        newJobs[job.id] = job
      }
      for (const job of (msg.recent_jobs || [])) {
        newJobs[job.id] = job
      }
      jobs.value = newJobs
      // Forward to listeners
      messageListeners.value.forEach(l => l(msg))
      return
    }

    // Handle pong (keep-alive response)
    if (msg.type === 'pong') return

    const jobId = msg.job_id
    if (!jobId) {
      // Forward domain events without job_id
      messageListeners.value.forEach(l => l(msg))
      return
    }

    switch (msg.type) {
      case 'job_created':
      case 'job_queued':
        jobs.value[jobId] = {
          id: jobId,
          job_type: msg.job_type || 'benchmark',
          status: msg.status || 'pending',
          progress_pct: msg.progress_pct ?? 0,
          progress_detail: msg.progress_detail || (JOB_TYPE_LABELS[msg.job_type] || msg.job_type) + ' queued',
          result_ref: null,
          created_at: msg.timestamp || new Date().toISOString(),
          started_at: null,
          completed_at: null,
          error_msg: null,
        }
        break

      case 'job_started':
        if (jobs.value[jobId]) {
          jobs.value[jobId] = {
            ...jobs.value[jobId],
            status: 'running',
            started_at: msg.timestamp || new Date().toISOString(),
            progress_detail: msg.progress_detail || jobs.value[jobId].progress_detail,
          }
        }
        break

      case 'job_progress':
        if (jobs.value[jobId]) {
          jobs.value[jobId] = {
            ...jobs.value[jobId],
            progress_pct: msg.progress_pct ?? jobs.value[jobId].progress_pct,
            progress_detail: msg.progress_detail || jobs.value[jobId].progress_detail,
            status: 'running',
          }
        }
        break

      case 'job_completed':
        if (jobs.value[jobId]) {
          jobs.value[jobId] = {
            ...jobs.value[jobId],
            status: 'done',
            progress_pct: 100,
            completed_at: msg.timestamp || new Date().toISOString(),
            result_ref: msg.result_ref || jobs.value[jobId].result_ref,
            progress_detail: msg.progress_detail || jobs.value[jobId].progress_detail,
          }
        }
        break

      case 'job_failed':
        if (jobs.value[jobId]) {
          jobs.value[jobId] = {
            ...jobs.value[jobId],
            status: 'failed',
            completed_at: msg.timestamp || new Date().toISOString(),
            error_msg: msg.error || msg.error_msg || 'Unknown error',
            progress_detail: msg.progress_detail || jobs.value[jobId].progress_detail,
          }
        }
        break

      case 'job_cancelled':
        if (jobs.value[jobId]) {
          jobs.value[jobId] = {
            ...jobs.value[jobId],
            status: 'cancelled',
            completed_at: msg.timestamp || new Date().toISOString(),
          }
        }
        break

      default:
        // Domain-specific events (benchmark_*, tool_eval_*, tune_*, etc.)
        // Forward to listeners without modifying job store
        break
    }

    // Forward all messages to listeners for domain-specific handling
    messageListeners.value.forEach(l => l(msg))
  }

  function trackFailure() {
    const now = Date.now()
    if (now - failWindowStart > FAIL_WINDOW_MS) {
      failCount = 0
      failWindowStart = now
    }
    failCount++
    if (failCount >= FAIL_THRESHOLD) {
      wsBannerVisible.value = true
    }
  }

  function toggleDropdown() {
    dropdownOpen.value = !dropdownOpen.value
  }

  function closeDropdown() {
    dropdownOpen.value = false
  }

  return {
    jobs,
    wsStatus: computedWsStatus,
    dropdownOpen,
    wsBannerVisible,
    activeJobs,
    recentJobs,
    activeCount,
    connect,
    disconnect,
    hydrate,
    cancelJob,
    handleMessage,
    toggleDropdown,
    closeDropdown,
    onMessage,
    send,
  }
})
