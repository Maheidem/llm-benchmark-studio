import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiFetch } from '../utils/api.js'
import { useActiveSession } from '../composables/useActiveSession.js'

export const useParamTunerStore = defineStore('paramTuner', () => {
  // --- State ---
  const searchSpace = ref({})
  const results = ref([])
  const history = ref([])
  const activeRunId = ref(null)
  const activeJobId = ref(null)
  const isRunning = ref(false)
  const progress = ref({ pct: 0, detail: '', eta: '' })
  const compatMatrix = ref(null)
  const totalCombos = ref(0)
  const sortKey = ref('overall_score')
  const sortAsc = ref(false)

  const session = useActiveSession()

  // --- Getters ---
  const bestConfig = computed(() => {
    if (results.value.length === 0) return null
    const sorted = [...results.value].sort((a, b) => (b.overall_score || 0) - (a.overall_score || 0))
    return sorted[0] || null
  })

  const bestScore = computed(() => bestConfig.value?.overall_score || 0)

  const sortedResults = computed(() => {
    const key = sortKey.value
    const asc = sortAsc.value
    return [...results.value].sort((a, b) => {
      const va = a[key] ?? 0
      const vb = b[key] ?? 0
      return asc ? va - vb : vb - va
    })
  })

  // --- Session Storage ---
  const PT_KEY = '_ptJobId'
  const PT_RUN_KEY = '_ptRunId'

  function persistJob() {
    try {
      if (activeJobId.value) sessionStorage.setItem(PT_KEY, activeJobId.value)
      if (activeRunId.value) sessionStorage.setItem(PT_RUN_KEY, activeRunId.value)
    } catch { /* ignore */ }
  }

  function restoreJob() {
    try {
      const jobId = sessionStorage.getItem(PT_KEY)
      const runId = sessionStorage.getItem(PT_RUN_KEY)
      if (jobId) {
        activeJobId.value = jobId
        activeRunId.value = runId
        isRunning.value = true
      }
    } catch { /* ignore */ }
  }

  function clearSession() {
    try {
      sessionStorage.removeItem(PT_KEY)
      sessionStorage.removeItem(PT_RUN_KEY)
    } catch { /* ignore */ }
  }

  // --- Actions ---

  async function startTuning(body) {
    // Clear results BEFORE API call to prevent stale data flash
    results.value = []
    totalCombos.value = 0
    progress.value = { pct: 0, detail: 'Starting...', eta: '' }
    session.startTracking()

    const res = await apiFetch('/api/tool-eval/param-tune', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (res.status === 409) throw new Error('A job is already running')
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || `Server error (${res.status})`)
    }
    const data = await res.json()
    activeJobId.value = data.job_id
    isRunning.value = true
    persistJob()
    return data
  }

  async function cancelTuning() {
    if (!activeJobId.value) return
    const res = await apiFetch('/api/tool-eval/param-tune/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: activeJobId.value }),
    })
    if (!res.ok) throw new Error('Failed to cancel')
  }

  async function loadHistory() {
    const res = await apiFetch('/api/tool-eval/param-tune/history')
    if (!res.ok) throw new Error('Failed to load history')
    const data = await res.json()
    history.value = data.runs || []
    return history.value
  }

  async function loadRun(id) {
    const res = await apiFetch(`/api/tool-eval/param-tune/history/${id}`)
    if (!res.ok) throw new Error('Run not found')
    const data = await res.json()
    // Populate results from loaded run
    if (data.results_json) {
      try {
        const parsed = typeof data.results_json === 'string'
          ? JSON.parse(data.results_json)
          : data.results_json
        results.value = parsed
      } catch { results.value = [] }
    }
    return data
  }

  async function deleteRun(id) {
    const res = await apiFetch(`/api/tool-eval/param-tune/history/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error('Failed to delete')
    history.value = history.value.filter(r => r.id !== id)
  }

  async function loadCompatMatrix(targets) {
    // The compat matrix is built client-side from the param support registry
    // and Phase 10 settings. This is a convenience wrapper.
    try {
      const res = await apiFetch('/api/settings/phase10')
      if (res.ok) {
        const data = await res.json()
        compatMatrix.value = data.param_support || null
      }
    } catch {
      compatMatrix.value = null
    }
  }

  function handleProgress(msg) {
    // Ignore events for a different job
    if (msg.job_id && activeJobId.value && msg.job_id !== activeJobId.value) return

    switch (msg.type) {
      case 'tune_start': {
        isRunning.value = true
        activeRunId.value = msg.tune_id || null
        totalCombos.value = msg.total_combos || 0
        results.value = []
        session.startTracking()
        progress.value = {
          pct: 0,
          detail: `Tuning ${msg.suite_name || ''}...`,
          eta: '',
        }
        persistJob()
        break
      }

      case 'combo_result': {
        const data = msg.data || msg
        results.value = [...results.value, data]
        session.recordStep()
        const completed = results.value.length
        const total = totalCombos.value || completed
        const pct = total > 0 ? Math.round((completed / total) * 100) : 0
        progress.value = {
          pct,
          detail: `${data.model_name || ''}, combo ${completed}/${total}`,
          eta: session.calculateETA(completed, total),
        }
        break
      }

      case 'job_progress': {
        progress.value = {
          pct: msg.progress_pct || progress.value.pct,
          detail: msg.progress_detail || progress.value.detail,
          eta: progress.value.eta,
        }
        break
      }

      case 'tune_complete': {
        isRunning.value = false
        progress.value = { pct: 100, detail: 'Complete!', eta: '' }
        activeJobId.value = null
        session.resetTracking()
        clearSession()
        break
      }

      case 'job_completed': {
        isRunning.value = false
        progress.value = { pct: 100, detail: 'Complete!', eta: '' }
        activeJobId.value = null
        session.resetTracking()
        clearSession()
        break
      }

      case 'job_failed': {
        isRunning.value = false
        progress.value = {
          pct: progress.value.pct,
          detail: msg.error || msg.error_msg || 'Failed',
          eta: '',
        }
        activeJobId.value = null
        session.resetTracking()
        clearSession()
        break
      }

      case 'job_cancelled': {
        isRunning.value = false
        progress.value = { pct: progress.value.pct, detail: 'Cancelled', eta: '' }
        activeJobId.value = null
        session.resetTracking()
        clearSession()
        break
      }
    }
  }

  function reset() {
    results.value = []
    isRunning.value = false
    activeJobId.value = null
    activeRunId.value = null
    progress.value = { pct: 0, detail: '', eta: '' }
    totalCombos.value = 0
    session.resetTracking()
    clearSession()
  }

  function setSort(key) {
    if (sortKey.value === key) {
      sortAsc.value = !sortAsc.value
    } else {
      sortKey.value = key
      sortAsc.value = false
    }
  }

  return {
    // State
    searchSpace,
    results,
    history,
    activeRunId,
    activeJobId,
    isRunning,
    progress,
    compatMatrix,
    totalCombos,
    sortKey,
    sortAsc,

    // Getters
    bestConfig,
    bestScore,
    sortedResults,

    // Actions
    startTuning,
    cancelTuning,
    loadHistory,
    loadRun,
    deleteRun,
    loadCompatMatrix,
    handleProgress,
    reset,
    restoreJob,
    setSort,
  }
})
