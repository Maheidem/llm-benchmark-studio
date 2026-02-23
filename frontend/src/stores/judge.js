import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiFetch } from '../utils/api.js'

export const useJudgeStore = defineStore('judge', () => {
  // --- State ---
  const reports = ref([])
  const currentReport = ref(null)
  const activeJobId = ref(null)
  const isRunning = ref(false)
  const progress = ref({ pct: 0, detail: '' })
  const verdicts = ref([])
  const modelReports = ref([])

  // --- Getters ---
  const sortedReports = computed(() => {
    return [...reports.value].sort((a, b) =>
      (b.timestamp || b.created_at || '').localeCompare(a.timestamp || a.created_at || '')
    )
  })

  // --- Session Storage ---
  const JG_KEY = '_jgJobId'

  function persistJob() {
    try {
      if (activeJobId.value) sessionStorage.setItem(JG_KEY, activeJobId.value)
    } catch { /* ignore */ }
  }

  function restoreJob() {
    try {
      const jobId = sessionStorage.getItem(JG_KEY)
      if (jobId) {
        activeJobId.value = jobId
        isRunning.value = true
      }
    } catch { /* ignore */ }
  }

  function clearSession() {
    try { sessionStorage.removeItem(JG_KEY) } catch { /* ignore */ }
  }

  // --- Actions ---

  async function runJudge(body) {
    const res = await apiFetch('/api/tool-eval/judge', {
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
    verdicts.value = []
    modelReports.value = []
    progress.value = { pct: 0, detail: 'Submitted...' }
    persistJob()
    return data
  }

  async function runCompare(body) {
    const res = await apiFetch('/api/tool-eval/judge/compare', {
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
    verdicts.value = []
    progress.value = { pct: 0, detail: 'Submitted...' }
    persistJob()
    return data
  }

  async function cancelJudge() {
    if (!activeJobId.value) return
    const res = await apiFetch('/api/tool-eval/judge/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: activeJobId.value }),
    })
    if (!res.ok) throw new Error('Failed to cancel')
  }

  async function loadReports() {
    const res = await apiFetch('/api/tool-eval/judge/reports')
    if (!res.ok) throw new Error('Failed to load reports')
    const data = await res.json()
    reports.value = data.reports || []
    return reports.value
  }

  async function loadReport(id) {
    const res = await apiFetch(`/api/tool-eval/judge/reports/${id}`)
    if (!res.ok) throw new Error('Report not found')
    const data = await res.json()
    currentReport.value = data
    // Parse nested JSON fields
    if (data.verdicts_json) {
      try {
        data._verdicts = typeof data.verdicts_json === 'string'
          ? JSON.parse(data.verdicts_json) : data.verdicts_json
      } catch { data._verdicts = [] }
    }
    if (data.report_json) {
      try {
        data._reports = typeof data.report_json === 'string'
          ? JSON.parse(data.report_json) : data.report_json
      } catch { data._reports = [] }
    }
    return data
  }

  async function deleteReport(id) {
    const res = await apiFetch(`/api/tool-eval/judge/reports/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error('Failed to delete')
    reports.value = reports.value.filter(r => r.id !== id)
  }

  async function rerunJudge(body) {
    const res = await apiFetch('/api/tool-eval/judge/rerun', {
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
    verdicts.value = []
    modelReports.value = []
    progress.value = { pct: 0, detail: `Re-running v${data.version || '?'}...` }
    persistJob()
    return data
  }

  async function fetchVersions(reportId) {
    const res = await apiFetch(`/api/tool-eval/judge/reports/${reportId}/versions`)
    if (!res.ok) throw new Error('Failed to load versions')
    const data = await res.json()
    return data.versions || []
  }

  function handleProgress(msg) {
    switch (msg.type) {
      case 'judge_start': {
        isRunning.value = true
        verdicts.value = []
        modelReports.value = []
        progress.value = {
          pct: 0,
          detail: `Judging with ${msg.judge_model || ''}...`,
        }
        persistJob()
        break
      }

      case 'judge_verdict': {
        verdicts.value = [...verdicts.value, msg]
        break
      }

      case 'judge_report': {
        if (msg.report) {
          modelReports.value = [...modelReports.value, msg.report]
        }
        break
      }

      case 'judge_complete': {
        isRunning.value = false
        progress.value = { pct: 100, detail: 'Complete!' }
        activeJobId.value = null
        clearSession()
        break
      }

      case 'job_progress': {
        progress.value = {
          pct: msg.progress_pct ?? progress.value.pct,
          detail: msg.progress_detail || progress.value.detail,
        }
        break
      }

      case 'job_completed': {
        isRunning.value = false
        progress.value = { pct: 100, detail: 'Complete!' }
        activeJobId.value = null
        clearSession()
        break
      }

      case 'job_failed': {
        isRunning.value = false
        progress.value = {
          pct: progress.value.pct,
          detail: msg.error || msg.error_msg || 'Failed',
        }
        activeJobId.value = null
        clearSession()
        break
      }

      case 'job_cancelled': {
        isRunning.value = false
        progress.value = { ...progress.value, detail: 'Cancelled' }
        activeJobId.value = null
        clearSession()
        break
      }

      // Compare-specific events
      case 'compare_case': {
        verdicts.value = [...verdicts.value, msg]
        break
      }

      case 'compare_complete': {
        isRunning.value = false
        progress.value = { pct: 100, detail: 'Compare complete!' }
        activeJobId.value = null
        clearSession()
        break
      }
    }
  }

  function reset() {
    verdicts.value = []
    modelReports.value = []
    currentReport.value = null
    isRunning.value = false
    activeJobId.value = null
    progress.value = { pct: 0, detail: '' }
    clearSession()
  }

  return {
    // State
    reports,
    currentReport,
    activeJobId,
    isRunning,
    progress,
    verdicts,
    modelReports,

    // Getters
    sortedReports,

    // Actions
    runJudge,
    runCompare,
    cancelJudge,
    loadReports,
    loadReport,
    deleteReport,
    rerunJudge,
    fetchVersions,
    handleProgress,
    reset,
    restoreJob,
  }
})
