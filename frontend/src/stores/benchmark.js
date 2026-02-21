import { defineStore } from 'pinia'
import { ref, computed, reactive } from 'vue'
import { apiFetch } from '../utils/api.js'
import { useConfigStore } from './config.js'
import { getColor } from '../utils/constants.js'
import { formatCtxSize } from '../utils/helpers.js'
import { useActiveSession } from '../composables/useActiveSession.js'

export const useBenchmarkStore = defineStore('benchmark', () => {
  // ── Selection state ──
  const selectedModels = ref(new Set())
  const contextTiers = ref(new Set([0]))
  const maxTokens = ref(512)
  const temperature = ref(0.7)
  const runs = ref(1)
  const prompt = ref('')
  const warmup = ref(false)
  const promptTemplates = ref([])

  // ── Execution state ──
  const isRunning = ref(false)
  const activeJobId = ref(null)
  const lastBenchmarkBody = ref(null)
  const currentResults = ref([])
  const providerProgress = reactive({})
  const skippedModels = ref([])
  const errorBanner = ref(null)

  const session = useActiveSession()

  // ── Computed ──
  const selectedCount = computed(() => selectedModels.value.size)

  const isStressMode = computed(() => {
    const tiers = new Set(currentResults.value.map(r => r.context_tokens || 0))
    return tiers.size > 1
  })

  const aggregatedResults = computed(() => {
    const grouped = {}
    for (const r of currentResults.value) {
      const key = `${r.model_id}::${r.provider}::${r.context_tokens || 0}`
      if (!grouped[key]) grouped[key] = { ...r, _runs: [], _successes: 0 }
      grouped[key]._runs.push(r)
      if (r.success) grouped[key]._successes++
    }
    return Object.values(grouped).map(g => {
      const successes = g._runs.filter(r => r.success)
      const n = successes.length
      const tpsVals = successes.map(r => r.tokens_per_second).sort((a, b) => a - b)
      const ttftVals = successes.map(r => r.ttft_ms).sort((a, b) => a - b)
      const inputTpsVals = successes.map(r => r.input_tokens_per_second || 0).filter(v => v > 0)
      const costVals = successes.map(r => r.cost || 0)
      return {
        provider: g.provider,
        model: g.model,
        model_id: g.model_id,
        context_tokens: g.context_tokens || 0,
        tokens_per_second: n ? successes.reduce((s, r) => s + r.tokens_per_second, 0) / n : 0,
        ttft_ms: n ? successes.reduce((s, r) => s + r.ttft_ms, 0) / n : 0,
        total_time_s: n ? successes.reduce((s, r) => s + r.total_time_s, 0) / n : 0,
        output_tokens: n ? successes.reduce((s, r) => s + r.output_tokens, 0) / n : 0,
        input_tokens_per_second: inputTpsVals.length ? inputTpsVals.reduce((s, v) => s + v, 0) / inputTpsVals.length : 0,
        avg_cost: n ? costVals.reduce((s, v) => s + v, 0) / n : 0,
        total_cost: costVals.reduce((s, v) => s + v, 0),
        std_dev_tps: stdDev(tpsVals),
        std_dev_ttft: stdDev(ttftVals),
        success: g._successes > 0,
        runs: g._runs.length,
        failures: g._runs.length - g._successes,
        error: g._runs.find(r => !r.success)?.error || '',
      }
    }).sort((a, b) => b.tokens_per_second - a.tokens_per_second)
  })

  const overallProgress = computed(() => {
    let totalCompleted = 0
    let totalAll = 0
    for (const p of Object.values(providerProgress)) {
      totalCompleted += p.completedSteps
      totalAll += p.totalSteps
    }
    return { completed: totalCompleted, total: totalAll }
  })

  const overallLabel = computed(() => {
    const running = Object.entries(providerProgress).filter(([, p]) => p.status === 'running')
    if (running.length > 0) {
      return `Running ${running.length} provider${running.length > 1 ? 's' : ''} in parallel`
    }
    return isRunning.value ? 'Benchmark running...' : ''
  })

  const eta = computed(() => {
    const { completed, total } = overallProgress.value
    if (!isRunning.value || total === 0) return ''
    return session.calculateETA(completed, total)
  })

  // ── Model selection ──
  function toggleModel(compoundKey) {
    const s = new Set(selectedModels.value)
    if (s.has(compoundKey)) s.delete(compoundKey)
    else s.add(compoundKey)
    selectedModels.value = s
  }

  function selectAll() {
    const configStore = useConfigStore()
    const s = new Set()
    for (const m of configStore.allModels) {
      s.add(m.compoundKey)
    }
    selectedModels.value = s
  }

  function selectNone() {
    selectedModels.value = new Set()
  }

  function toggleProviderModels(providerName) {
    const configStore = useConfigStore()
    const providerModels = configStore.allModels.filter(m => m.provider === providerName)
    const keys = providerModels.map(m => m.compoundKey)
    const allSelected = keys.every(k => selectedModels.value.has(k))
    const s = new Set(selectedModels.value)
    if (allSelected) {
      keys.forEach(k => s.delete(k))
    } else {
      keys.forEach(k => s.add(k))
    }
    selectedModels.value = s
  }

  // ── Tier selection ──
  function toggleTier(value) {
    const s = new Set(contextTiers.value)
    if (s.has(value)) {
      s.delete(value)
      if (s.size === 0) s.add(0)
    } else {
      s.add(value)
    }
    contextTiers.value = s
  }

  // ── Progress handling ──
  let _pendingInit = null

  function _applyBenchmarkInit(msg) {
    initProviderProgress(msg.data)
    // On reconnect, pre-seed completed steps from progress_pct
    // so the counter doesn't start from 0/N
    if (msg.reconnect && msg.progress_pct > 0) {
      const pct = msg.progress_pct / 100
      for (const pp of Object.values(providerProgress)) {
        const seeded = Math.round(pp.totalSteps * pct)
        pp.completedSteps = seeded
        if (seeded > 0) pp.status = 'running'
      }
    }
    _pendingInit = null
  }

  function replayPendingInit() {
    if (_pendingInit) {
      _applyBenchmarkInit(_pendingInit)
    }
  }

  function initProviderProgress(body) {
    // Clear existing
    Object.keys(providerProgress).forEach(k => delete providerProgress[k])

    const configStore = useConfigStore()
    if (!configStore.config?.providers) return

    let selectedSet
    if (body.targets && Array.isArray(body.targets)) {
      selectedSet = new Set(body.targets.map(t => t.provider_key + '::' + t.model_id))
    } else {
      selectedSet = new Set((body.models || []).map(m => typeof m === 'string' ? m : ''))
    }
    const runCount = body.runs || 1
    const tiers = body.context_tiers || [0]
    const mTokens = body.max_tokens || 4096

    for (const [provider, provData] of Object.entries(configStore.config.providers)) {
      const models = configStore.getProviderModels(provData)
      const pk = provData.provider_key || provider
      const selectedInProvider = body.targets
        ? models.filter(m => selectedSet.has(pk + '::' + m.model_id))
        : models.filter(m => selectedSet.has(m.model_id))
      if (selectedInProvider.length === 0) continue

      let totalSteps = 0
      for (const m of selectedInProvider) {
        for (const tier of tiers) {
          const headroom = (m.context_window || Infinity) - mTokens - 100
          if (tier === 0 || tier <= headroom) {
            totalSteps += runCount
          }
        }
      }

      providerProgress[provider] = {
        currentModel: null,
        currentRun: 0,
        totalRuns: runCount,
        completedSteps: 0,
        totalSteps,
        status: 'waiting',
        errors: [],
        currentContextTokens: 0,
      }
    }
  }

  function handleSSE(data) {
    const prov = data.provider

    if (data.type === 'progress') {
      if (prov && providerProgress[prov]) {
        const pp = providerProgress[prov]
        pp.status = 'running'
        pp.currentModel = data.model
        pp.currentRun = data.run
        pp.totalRuns = data.runs
        pp.currentContextTokens = data.context_tokens || 0
      }
    }
    if (data.type === 'skipped') {
      skippedModels.value = [...skippedModels.value, {
        model: data.model,
        reason: data.reason || 'context exceeds window',
      }]
    }
    if (data.type === 'result') {
      session.recordStep()
      if (prov && providerProgress[prov]) {
        const pp = providerProgress[prov]
        pp.completedSteps++
        if (!data.success && data.error) {
          pp.errors.push({ model: data.model, message: data.error })
        }
        if (pp.completedSteps >= pp.totalSteps) {
          pp.status = 'done'
          pp.currentModel = null
        }
      }
      currentResults.value = [...currentResults.value, data]
    }
    if (data.type === 'error') {
      if (prov && providerProgress[prov]) {
        const pp = providerProgress[prov]
        pp.errors.push({ model: data.model || '?', message: data.message || 'Unknown error' })
        pp.completedSteps = pp.totalSteps
        pp.status = 'error'
        pp.currentModel = null
      } else {
        errorBanner.value = data.message || 'Benchmark error'
      }
    }
    if (data.type === 'complete') {
      for (const pp of Object.values(providerProgress)) {
        if (pp.status !== 'done' && pp.status !== 'error') {
          pp.status = 'done'
          pp.completedSteps = pp.totalSteps
          pp.currentModel = null
        }
      }
      isRunning.value = false
    }
  }

  function handleJobEvent(msg) {
    if (!activeJobId.value || msg.job_id !== activeJobId.value) return

    switch (msg.type) {
      case 'job_started':
        break

      case 'job_progress':
        break

      case 'benchmark_init':
        if (msg.data) {
          const configStore = useConfigStore()
          if (!configStore.config?.providers) {
            // Config not loaded yet — save for replay after config loads
            _pendingInit = msg
          } else {
            _applyBenchmarkInit(msg)
          }
        }
        break

      case 'benchmark_progress':
        if (msg.data) handleSSE(msg.data)
        break

      case 'benchmark_result':
        if (msg.data) handleSSE(msg.data)
        break

      case 'job_completed':
        handleSSE({ type: 'complete' })
        activeJobId.value = null
        isRunning.value = false
        session.resetTracking()
        loadFinalResults(msg.result_ref)
        break

      case 'job_failed':
        activeJobId.value = null
        isRunning.value = false
        session.resetTracking()
        errorBanner.value = msg.error || msg.error_msg || 'Benchmark failed'
        break

      case 'job_cancelled':
        activeJobId.value = null
        isRunning.value = false
        session.resetTracking()
        break
    }
  }

  async function loadFinalResults(resultRef) {
    if (!resultRef) return
    try {
      const res = await apiFetch(`/api/history/${resultRef}`)
      if (!res.ok) return
      const data = await res.json()
      currentResults.value = (data.results || []).map(r => ({
        ...r,
        type: 'result',
        success: r.success !== false,
      }))
    } catch (e) {
      console.error('Failed to load benchmark results:', e)
    }
  }

  // ── Start benchmark ──
  async function startBenchmark(overrideBody) {
    if (!overrideBody && selectedModels.value.size === 0) {
      throw new Error('Select at least one model')
    }

    isRunning.value = true
    currentResults.value = []
    skippedModels.value = []
    errorBanner.value = null
    session.startTracking()

    const body = overrideBody || {
      targets: Array.from(selectedModels.value).map(k => parseCompoundKey(k)),
      runs: runs.value,
      max_tokens: maxTokens.value,
      temperature: temperature.value,
      prompt: prompt.value,
      context_tiers: Array.from(contextTiers.value).sort((a, b) => a - b),
      warmup: warmup.value,
    }
    lastBenchmarkBody.value = body

    initProviderProgress(body)

    const res = await apiFetch('/api/benchmark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    if (!res.ok) {
      isRunning.value = false
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || `Server error (${res.status})`)
    }

    const data = await res.json()
    activeJobId.value = data.job_id
    return data
  }

  async function cancelBenchmark() {
    if (activeJobId.value) {
      await apiFetch(`/api/jobs/${activeJobId.value}/cancel`, { method: 'POST' })
    }
  }

  function restoreRunningJob(job) {
    activeJobId.value = job.id
    isRunning.value = true
  }

  // ── Helpers ──
  function parseCompoundKey(key) {
    const i = key.indexOf('::')
    return { provider_key: key.substring(0, i), model_id: key.substring(i + 2) }
  }

  function stdDev(values) {
    if (values.length < 2) return 0
    const mean = values.reduce((s, v) => s + v, 0) / values.length
    const variance = values.reduce((s, v) => s + (v - mean) ** 2, 0) / (values.length - 1)
    return Math.sqrt(variance)
  }

  // ── Prompt templates ──
  async function loadPromptTemplates() {
    try {
      const res = await apiFetch('/api/config/prompts')
      const data = await res.json()
      // API returns { key: { label, prompt }, ... }
      promptTemplates.value = Object.entries(data).map(([key, tpl]) => ({
        key,
        label: tpl.label || key,
        prompt: tpl.prompt || '',
      }))
    } catch (e) {
      console.error('Failed to load prompt templates:', e)
    }
  }

  function applyPromptTemplate(key) {
    if (!key) return
    const tpl = promptTemplates.value.find(t => t.key === key)
    if (tpl) {
      prompt.value = tpl.prompt
    }
  }

  // ── Apply defaults from config ──
  function applyDefaults(configObj) {
    if (configObj?.defaults?.prompt) {
      prompt.value = configObj.defaults.prompt.trim()
    }
    if (configObj?.defaults?.max_tokens) {
      maxTokens.value = configObj.defaults.max_tokens
    }
    if (configObj?.defaults?.temperature !== undefined) {
      temperature.value = configObj.defaults.temperature
    }
  }

  // ── Initialize selectedModels to all ──
  function selectAllFromConfig(configObj) {
    if (!configObj?.providers) return
    const s = new Set()
    for (const [provider, provData] of Object.entries(configObj.providers)) {
      const models = Array.isArray(provData) ? provData : (provData.models || [])
      const pk = provData.provider_key || provider
      for (const m of models) {
        s.add(pk + '::' + m.model_id)
      }
    }
    selectedModels.value = s
  }

  return {
    // State
    selectedModels,
    contextTiers,
    maxTokens,
    temperature,
    runs,
    prompt,
    warmup,
    promptTemplates,
    isRunning,
    activeJobId,
    lastBenchmarkBody,
    currentResults,
    providerProgress,
    skippedModels,
    errorBanner,
    // Computed
    selectedCount,
    isStressMode,
    aggregatedResults,
    overallProgress,
    overallLabel,
    eta,
    // Actions
    toggleModel,
    selectAll,
    selectNone,
    toggleProviderModels,
    toggleTier,
    handleJobEvent,
    startBenchmark,
    cancelBenchmark,
    restoreRunningJob,
    replayPendingInit,
    loadPromptTemplates,
    applyPromptTemplate,
    applyDefaults,
    selectAllFromConfig,
    parseCompoundKey,
  }
})
