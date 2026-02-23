import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiFetch } from '../utils/api.js'
import { useActiveSession } from '../composables/useActiveSession.js'

export const usePromptTunerStore = defineStore('promptTuner', () => {
  // --- State ---
  const generations = ref([])
  const bestPrompt = ref(null)
  const bestScore = ref(0)
  const history = ref([])
  const activeRunId = ref(null)
  const activeJobId = ref(null)
  const isRunning = ref(false)
  const progress = ref({ pct: 0, detail: '', generation: 0, totalGenerations: 0 })
  const mode = ref('quick')
  const totalPrompts = ref(0)
  const completedPrompts = ref(0)

  const session = useActiveSession()

  // --- Getters ---
  const currentGeneration = computed(() => {
    if (generations.value.length === 0) return null
    return generations.value[generations.value.length - 1]
  })

  const bestScoreGetter = computed(() => bestScore.value)

  const generationScores = computed(() => {
    return generations.value.map(g => ({
      generation: g.generation,
      bestScore: g.best_score || 0,
    }))
  })

  // --- Session Storage ---
  const PRT_KEY = '_prtJobId'
  const PRT_RUN_KEY = '_prtRunId'

  function persistJob() {
    try {
      if (activeJobId.value) sessionStorage.setItem(PRT_KEY, activeJobId.value)
      if (activeRunId.value) sessionStorage.setItem(PRT_RUN_KEY, activeRunId.value)
    } catch { /* ignore */ }
  }

  function restoreJob() {
    try {
      const jobId = sessionStorage.getItem(PRT_KEY)
      const runId = sessionStorage.getItem(PRT_RUN_KEY)
      if (jobId) {
        activeJobId.value = jobId
        activeRunId.value = runId
        isRunning.value = true
      }
    } catch { /* ignore */ }
  }

  function clearSession() {
    try {
      sessionStorage.removeItem(PRT_KEY)
      sessionStorage.removeItem(PRT_RUN_KEY)
    } catch { /* ignore */ }
  }

  // --- Actions ---

  async function startTuning(body) {
    // Clear results BEFORE API call to prevent stale data flash
    generations.value = []
    bestPrompt.value = null
    bestScore.value = 0
    completedPrompts.value = 0
    totalPrompts.value = 0
    progress.value = { pct: 0, detail: 'Starting...', generation: 0, totalGenerations: 0 }
    session.startTracking()

    const res = await apiFetch('/api/tool-eval/prompt-tune', {
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
    const res = await apiFetch('/api/tool-eval/prompt-tune/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: activeJobId.value }),
    })
    if (!res.ok) throw new Error('Failed to cancel')
  }

  async function getEstimate(params) {
    const qs = new URLSearchParams(params).toString()
    const res = await apiFetch(`/api/tool-eval/prompt-tune/estimate?${qs}`)
    if (!res.ok) throw new Error('Failed to get estimate')
    return await res.json()
  }

  async function loadHistory() {
    const res = await apiFetch('/api/tool-eval/prompt-tune/history')
    if (!res.ok) throw new Error('Failed to load history')
    const data = await res.json()
    history.value = data.runs || []
    return history.value
  }

  async function loadRun(id) {
    const res = await apiFetch(`/api/tool-eval/prompt-tune/history/${id}`)
    if (!res.ok) throw new Error('Run not found')
    const data = await res.json()
    // Parse generations
    if (data.generations_json) {
      try {
        generations.value = typeof data.generations_json === 'string'
          ? JSON.parse(data.generations_json)
          : data.generations_json
      } catch { generations.value = [] }
    }
    bestPrompt.value = data.best_prompt || null
    bestScore.value = data.best_score || 0
    return data
  }

  async function deleteRun(id) {
    const res = await apiFetch(`/api/tool-eval/prompt-tune/history/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error('Failed to delete')
    history.value = history.value.filter(r => r.id !== id)
  }

  function handleProgress(msg) {
    // Ignore events for a different job
    if (msg.job_id && activeJobId.value && msg.job_id !== activeJobId.value) return

    switch (msg.type) {
      case 'tune_start': {
        isRunning.value = true
        activeRunId.value = msg.tune_id || null
        mode.value = msg.mode || 'quick'
        totalPrompts.value = msg.total_prompts || 0
        completedPrompts.value = 0
        generations.value = []
        bestPrompt.value = null
        bestScore.value = 0
        session.startTracking()
        progress.value = {
          pct: 0,
          detail: `Tuning ${msg.suite_name || ''}...`,
          generation: 0,
          totalGenerations: 0,
        }
        persistJob()
        break
      }

      case 'generation_start': {
        progress.value = {
          ...progress.value,
          detail: `Generation ${msg.generation}/${msg.total_generations}`,
          generation: msg.generation,
          totalGenerations: msg.total_generations,
        }
        break
      }

      case 'prompt_generated': {
        // Individual prompt generated by meta-model
        break
      }

      case 'prompt_eval_start': {
        session.recordStep()
        progress.value = {
          ...progress.value,
          detail: `Gen ${msg.generation}: evaluating prompt ${msg.prompt_index + 1} on ${msg.model}`,
        }
        break
      }

      case 'prompt_eval_result': {
        // Individual prompt eval result for a model
        break
      }

      case 'generation_complete': {
        const gen = {
          generation: msg.generation,
          best_score: msg.best_score || 0,
          best_prompt_index: msg.best_prompt_index,
          survivors: msg.survivors || [],
        }
        generations.value = [...generations.value, gen]
        break
      }

      case 'generation_error': {
        // Meta model returned no prompts for this generation
        break
      }

      case 'job_progress': {
        completedPrompts.value = Math.round(
          (msg.progress_pct ?? 0) / 100 * totalPrompts.value
        )
        const eta = totalPrompts.value > 0
          ? session.calculateETA(completedPrompts.value, totalPrompts.value)
          : ''
        progress.value = {
          ...progress.value,
          pct: msg.progress_pct ?? progress.value.pct,
          detail: msg.progress_detail || progress.value.detail,
          eta,
        }
        break
      }

      case 'tune_complete': {
        isRunning.value = false
        bestPrompt.value = msg.best_prompt || null
        bestScore.value = msg.best_score || 0
        progress.value = { pct: 100, detail: 'Complete!', generation: 0, totalGenerations: 0 }
        activeJobId.value = null
        session.resetTracking()
        clearSession()
        break
      }

      case 'job_completed': {
        isRunning.value = false
        progress.value = { pct: 100, detail: 'Complete!', generation: 0, totalGenerations: 0 }
        activeJobId.value = null
        session.resetTracking()
        clearSession()
        break
      }

      case 'job_failed': {
        isRunning.value = false
        progress.value = {
          ...progress.value,
          detail: msg.error || msg.error_msg || 'Failed',
        }
        activeJobId.value = null
        session.resetTracking()
        clearSession()
        break
      }

      case 'job_cancelled': {
        isRunning.value = false
        progress.value = { ...progress.value, detail: 'Cancelled' }
        activeJobId.value = null
        session.resetTracking()
        clearSession()
        break
      }
    }
  }

  function reset() {
    generations.value = []
    bestPrompt.value = null
    bestScore.value = 0
    isRunning.value = false
    activeJobId.value = null
    activeRunId.value = null
    completedPrompts.value = 0
    totalPrompts.value = 0
    progress.value = { pct: 0, detail: '', generation: 0, totalGenerations: 0 }
    session.resetTracking()
    clearSession()
  }

  return {
    // State
    generations,
    bestPrompt,
    bestScore,
    history,
    activeRunId,
    activeJobId,
    isRunning,
    progress,
    mode,
    totalPrompts,
    completedPrompts,

    // Getters
    currentGeneration,
    bestScoreGetter,
    generationScores,

    // Actions
    startTuning,
    cancelTuning,
    getEstimate,
    loadHistory,
    loadRun,
    deleteRun,
    handleProgress,
    reset,
    restoreJob,
  }
})
