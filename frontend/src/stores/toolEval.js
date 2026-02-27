import { defineStore } from 'pinia'
import { ref, computed, reactive } from 'vue'
import { apiFetch } from '../utils/api.js'

export const useToolEvalStore = defineStore('toolEval', () => {
  // --- State ---
  const suites = ref([])
  const currentSuite = ref(null)
  const sharedContext = reactive({
    suiteId: null,
    suiteName: null,
    selectedModels: [],
    systemPrompts: {},       // { '_global': '...', 'model_id': '...' }
    temperature: 0.0,
    toolChoice: 'required',
    providerParams: {},
    experimentId: null,
    experimentName: null,
    lastUpdatedBy: null,     // 'param_tuner' | 'prompt_tuner' | 'judge' | null
    promptTunerHint: null,
  })
  const evalResults = ref([])
  const evalSummaries = ref([])
  const isEvaluating = ref(false)
  const activeJobId = ref(null)
  const evalStartTime = ref(null)
  const evalTotalCases = ref(0)
  const experiments = ref([])
  const history = ref([])
  const lastEvalId = ref(null)

  // --- Getters ---
  const suiteCount = computed(() => suites.value.length)
  const currentTools = computed(() => currentSuite.value?.tools || [])
  const currentTestCases = computed(() => currentSuite.value?.test_cases || [])
  const selectedModelCount = computed(() => sharedContext.selectedModels.length)

  // --- Session Storage ---
  const SC_KEY = '_sharedContext_vue'

  function saveContext() {
    try {
      sessionStorage.setItem(SC_KEY, JSON.stringify({
        suiteId: sharedContext.suiteId,
        suiteName: sharedContext.suiteName,
        selectedModels: sharedContext.selectedModels,
        systemPrompts: sharedContext.systemPrompts,
        temperature: sharedContext.temperature,
        toolChoice: sharedContext.toolChoice,
        providerParams: sharedContext.providerParams,
        experimentId: sharedContext.experimentId,
        experimentName: sharedContext.experimentName,
        lastUpdatedBy: sharedContext.lastUpdatedBy,
      }))
    } catch { /* ignore */ }
  }

  function loadContext() {
    try {
      const raw = sessionStorage.getItem(SC_KEY)
      if (raw) {
        const data = JSON.parse(raw)
        Object.assign(sharedContext, data)
      }
    } catch { /* ignore */ }
  }

  // --- Actions ---

  async function loadSuites() {
    try {
      const res = await apiFetch('/api/tool-suites')
      if (!res.ok) throw new Error('Failed to load suites')
      const data = await res.json()
      suites.value = data.suites || []
    } catch (e) {
      console.error('loadSuites:', e)
      throw e
    }
  }

  async function loadSuite(id) {
    try {
      const res = await apiFetch(`/api/tool-suites/${id}`)
      if (!res.ok) throw new Error('Suite not found')
      currentSuite.value = await res.json()
      return currentSuite.value
    } catch (e) {
      console.error('loadSuite:', e)
      throw e
    }
  }

  async function createSuite(data) {
    const res = await apiFetch('/api/tool-suites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || 'Failed to create suite')
    }
    const result = await res.json()
    return result
  }

  async function updateSuite(id, data) {
    const res = await apiFetch(`/api/tool-suites/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || 'Failed to update suite')
    }
    return await res.json()
  }

  async function patchSuite(id, data) {
    const res = await apiFetch(`/api/tool-suites/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) throw new Error('Failed to patch suite')
    return await res.json()
  }

  async function deleteSuite(id) {
    const res = await apiFetch(`/api/tool-suites/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error('Failed to delete suite')
    suites.value = suites.value.filter(s => s.id !== id)
  }

  async function exportSuite(id) {
    const res = await apiFetch(`/api/tool-suites/${id}/export`)
    if (!res.ok) throw new Error('Failed to export suite')
    const blob = await res.blob()
    const cd = res.headers.get('content-disposition') || ''
    const match = cd.match(/filename=([^\s;]+)/)
    const filename = match ? match[1] : `suite-${id.slice(0, 8)}.json`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  async function importSuite(data) {
    const res = await apiFetch('/api/tool-eval/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || 'Import failed')
    }
    return await res.json()
  }

  async function importBfclSuite(data, suiteName) {
    const res = await apiFetch('/api/tool-eval/import/bfcl', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Suite-Name': suiteName || 'BFCL Import',
      },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || 'BFCL import failed')
    }
    return await res.json()
  }

  async function smartImport(data, filename) {
    // Auto-detect format: BFCL if array or has function/question keys, standard otherwise
    const isBfcl = Array.isArray(data) ||
      (data && typeof data === 'object' && ('function' in data || 'question' in data))
    if (isBfcl) {
      const suiteName = (filename || '').replace(/\.json$/i, '') || 'BFCL Import'
      const entries = Array.isArray(data) ? data : [data]
      return await importBfclSuite(entries, suiteName)
    }
    // Standard format: must have name
    if (!data || !data.name || typeof data.name !== 'string') {
      throw new Error('Unrecognized format: expected a standard suite (with "name") or BFCL format (with "function"/"question")')
    }
    return await importSuite(data)
  }

  async function downloadImportExample() {
    const res = await apiFetch('/api/tool-eval/import/example')
    if (!res.ok) throw new Error('Failed to download example')
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'suite-example.json'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  // --- Test Cases ---

  async function createTestCase(suiteId, data) {
    const res = await apiFetch(`/api/tool-suites/${suiteId}/cases`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) throw new Error('Failed to create test case')
    return await res.json()
  }

  async function updateTestCase(suiteId, caseId, data) {
    const res = await apiFetch(`/api/tool-suites/${suiteId}/cases/${caseId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) throw new Error('Failed to update test case')
    return await res.json()
  }

  async function deleteTestCase(suiteId, caseId) {
    const res = await apiFetch(`/api/tool-suites/${suiteId}/cases/${caseId}`, {
      method: 'DELETE',
    })
    if (!res.ok) throw new Error('Failed to delete test case')
  }

  // --- Eval ---

  async function runEval(body) {
    const res = await apiFetch('/api/tool-eval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (res.status === 409) throw new Error('A benchmark or eval is already running')
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || `Server error (${res.status})`)
    }
    const data = await res.json()
    activeJobId.value = data.job_id
    isEvaluating.value = true
    evalStartTime.value = Date.now()
    try { sessionStorage.setItem('_teJobId', data.job_id) } catch {}
    return data
  }

  async function cancelEval() {
    if (activeJobId.value) {
      const res = await apiFetch('/api/tool-eval/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: activeJobId.value }),
      })
      if (!res.ok) throw new Error('Failed to cancel')
    } else {
      await apiFetch('/api/tool-eval/cancel', { method: 'POST' })
    }
  }

  function handleEvalProgress(msg) {
    switch (msg.type) {
      case 'job_started':
        isEvaluating.value = true
        evalStartTime.value = Date.now()
        break

      case 'tool_eval_init': {
        const data = msg.data || msg
        const targets = data.targets ? data.targets.length : 0
        evalTotalCases.value = targets * (data.total_cases || 0)
        break
      }

      case 'tool_eval_result': {
        const data = msg.data || msg
        evalResults.value.push(data)
        break
      }

      case 'tool_eval_progress': {
        const data = msg.data || msg
        evalTotalCases.value = data.total || evalTotalCases.value
        break
      }

      case 'tool_eval_summary': {
        const data = msg.data || msg
        evalSummaries.value.push(data)
        break
      }

      case 'tool_eval_complete': {
        const data = msg.data || msg
        isEvaluating.value = false
        if (data.eval_id) lastEvalId.value = data.eval_id
        activeJobId.value = null
        evalStartTime.value = null
        try { sessionStorage.removeItem('_teJobId') } catch {}
        break
      }

      case 'job_completed':
        isEvaluating.value = false
        activeJobId.value = null
        evalStartTime.value = null
        try { sessionStorage.removeItem('_teJobId') } catch {}
        break

      case 'job_failed':
        isEvaluating.value = false
        activeJobId.value = null
        evalStartTime.value = null
        try { sessionStorage.removeItem('_teJobId') } catch {}
        break

      case 'job_cancelled':
        isEvaluating.value = false
        activeJobId.value = null
        evalStartTime.value = null
        try { sessionStorage.removeItem('_teJobId') } catch {}
        break
    }
  }

  function resetEval() {
    evalResults.value = []
    evalSummaries.value = []
    isEvaluating.value = false
    activeJobId.value = null
    evalStartTime.value = null
    evalTotalCases.value = 0
    lastEvalId.value = null
  }

  // --- History ---

  async function loadHistory() {
    try {
      const res = await apiFetch('/api/tool-eval/history')
      if (!res.ok) throw new Error('Failed to load history')
      const data = await res.json()
      history.value = data.runs || []
    } catch (e) {
      console.error('loadHistory:', e)
      throw e
    }
  }

  async function loadHistoryRun(evalId) {
    const res = await apiFetch(`/api/tool-eval/history/${evalId}`)
    if (!res.ok) throw new Error('Eval run not found')
    return await res.json()
  }

  async function deleteHistoryRun(evalId) {
    const res = await apiFetch(`/api/tool-eval/history/${evalId}`, { method: 'DELETE' })
    if (!res.ok) throw new Error('Failed to delete eval run')
    history.value = history.value.filter(r => r.id !== evalId)
  }

  // --- Experiments ---

  async function loadExperiments(suiteId) {
    const url = suiteId ? `/api/experiments?suite_id=${suiteId}` : '/api/experiments'
    const res = await apiFetch(url)
    if (!res.ok) throw new Error('Failed to load experiments')
    const data = await res.json()
    experiments.value = data.experiments || data || []
    return experiments.value
  }

  // --- MCP ---

  async function mcpDiscover(url) {
    const res = await apiFetch('/api/mcp/discover', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    })
    const data = await res.json()
    if (data.error) throw new Error(data.error)
    return data
  }

  async function mcpImport(payload) {
    const res = await apiFetch('/api/mcp/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await res.json()
    if (data.error) throw new Error(data.error)
    return data
  }

  // --- Shared Context Helpers ---

  function setSuite(suiteId, suiteName) {
    sharedContext.suiteId = suiteId
    sharedContext.suiteName = suiteName
    sharedContext.lastUpdatedBy = null
    saveContext()
  }

  function setModels(models) {
    sharedContext.selectedModels = Array.isArray(models) ? models : Array.from(models)
    saveContext()
  }

  function setSystemPrompt(key, value) {
    if (!sharedContext.systemPrompts) sharedContext.systemPrompts = {}
    sharedContext.systemPrompts[key] = value
    saveContext()
  }

  function clearContext() {
    Object.assign(sharedContext, {
      suiteId: null,
      suiteName: null,
      selectedModels: [],
      systemPrompts: {},
      temperature: 0.0,
      toolChoice: 'required',
      providerParams: {},
      experimentId: null,
      experimentName: null,
      lastUpdatedBy: null,
      promptTunerHint: null,
    })
    saveContext()
  }

  return {
    // State
    suites,
    currentSuite,
    sharedContext,
    evalResults,
    evalSummaries,
    isEvaluating,
    activeJobId,
    evalStartTime,
    evalTotalCases,
    experiments,
    history,
    lastEvalId,

    // Getters
    suiteCount,
    currentTools,
    currentTestCases,
    selectedModelCount,

    // Actions
    loadSuites,
    loadSuite,
    createSuite,
    updateSuite,
    patchSuite,
    deleteSuite,
    exportSuite,
    importSuite,
    importBfclSuite,
    smartImport,
    downloadImportExample,
    createTestCase,
    updateTestCase,
    deleteTestCase,
    runEval,
    cancelEval,
    handleEvalProgress,
    resetEval,
    loadHistory,
    loadHistoryRun,
    deleteHistoryRun,
    loadExperiments,
    mcpDiscover,
    mcpImport,
    setSuite,
    setModels,
    setSystemPrompt,
    clearContext,
    saveContext,
    loadContext,
  }
})
