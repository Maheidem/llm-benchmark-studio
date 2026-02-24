<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Auto-Optimize</h2>
        <p class="text-sm text-zinc-600 font-body">Automatically generate and optimize system prompts using OPRO/APE techniques.</p>
      </div>
      <div class="flex items-center gap-2">
        <button
          v-if="isRunning"
          @click="viewActiveRun"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
          style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
        >View Active Run</button>
        <router-link :to="{ name: 'AutoOptimizeHistory' }"
          v-if="false"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
          style="border:1px solid var(--border-subtle);color:var(--zinc-400);"
        >History</router-link>
      </div>
    </div>

    <!-- Config form -->
    <div v-if="!isRunning && !runComplete" class="space-y-6">
      <!-- Suite selector -->
      <div class="card rounded-md p-5">
        <label class="section-label mb-2 block">Test Suite</label>
        <select
          v-model="config.suiteId"
          class="text-sm font-mono px-3 py-2 rounded-sm w-full"
          style="background:var(--surface);border:1px solid var(--border-subtle);color:#E4E4E7;outline:none;"
        >
          <option value="">-- Select a suite --</option>
          <option v-for="s in teStore.suites" :key="s.id" :value="s.id">
            {{ s.name }} ({{ s.tool_count || 0 }} tools, {{ s.test_case_count || 0 }} cases)
          </option>
        </select>
      </div>

      <!-- Base prompt -->
      <div class="card rounded-md p-5">
        <label class="section-label mb-2 block">Base System Prompt</label>
        <textarea
          v-model="config.basePrompt"
          rows="4"
          class="w-full text-xs font-body px-3 py-2 rounded-sm resize-y"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);color:#E4E4E7;outline:none;"
          placeholder="You are a helpful assistant that uses tools..."
        ></textarea>
      </div>

      <!-- Optimization model -->
      <div class="card rounded-md p-5">
        <label class="section-label mb-2 block">Optimization Model</label>
        <select
          v-model="config.optimizationModelKey"
          class="text-sm font-mono px-3 py-2 rounded-sm w-full"
          style="background:var(--surface);border:1px solid var(--border-subtle);color:#E4E4E7;outline:none;"
        >
          <option value="">-- Select model --</option>
          <option v-for="m in allModels" :key="m.compoundKey" :value="m.compoundKey">
            {{ m.display_name || m.model_id }} ({{ m.provider }})
          </option>
        </select>
        <p class="text-[10px] text-zinc-600 font-body mt-1">Model used to generate and mutate prompt variants.</p>
      </div>

      <!-- Parameters -->
      <div class="card rounded-md p-5">
        <span class="section-label mb-3 block">Parameters</span>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1 block">
              Max Iterations
              <span class="ml-1 text-zinc-700 cursor-help" title="Number of optimization iterations.">?</span>
            </label>
            <input
              v-model.number="config.maxIterations"
              type="number" min="1" max="20"
              class="w-full px-2 py-1.5 rounded-sm text-xs font-mono text-zinc-200"
              style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
            >
          </div>
          <div>
            <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1 block">
              Population Size
              <span class="ml-1 text-zinc-700 cursor-help" title="Number of prompt variants per iteration.">?</span>
            </label>
            <input
              v-model.number="config.populationSize"
              type="number" min="2" max="20"
              class="w-full px-2 py-1.5 rounded-sm text-xs font-mono text-zinc-200"
              style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
            >
          </div>
        </div>
      </div>

      <!-- Error -->
      <div v-if="startError" class="text-xs" style="color:var(--coral);">{{ startError }}</div>

      <!-- Start -->
      <div class="flex justify-end">
        <button
          @click="startAutoOptimize"
          :disabled="!canStart || starting"
          class="run-btn px-6 py-2 rounded-sm text-xs flex items-center gap-2"
          :class="{ 'opacity-50 cursor-not-allowed': !canStart || starting }"
        >
          <span v-if="starting" class="inline-block w-3.5 h-3.5 border-2 border-lime-400/30 border-t-lime-400 rounded-full animate-spin"></span>
          <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
          Start Auto-Optimize
        </button>
      </div>
    </div>

    <!-- Run in progress -->
    <div v-else-if="isRunning">
      <!-- Progress -->
      <div class="card rounded-md p-5 mb-6">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-3">
            <div class="pulse-dot"></div>
            <span class="text-sm text-zinc-400 font-body">{{ runProgress.detail }}</span>
          </div>
          <div class="flex items-center gap-3">
            <span v-if="runProgress.eta" class="text-[10px] font-mono text-zinc-600">{{ runProgress.eta }}</span>
            <span class="text-xs font-mono text-zinc-600">{{ runProgress.pct }}%</span>
            <button
              @click="cancelRun"
              class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm ml-2"
              style="color:var(--coral);border:1px solid rgba(255,59,92,0.3);"
            >Cancel</button>
          </div>
        </div>
        <div class="progress-track rounded-full overflow-hidden">
          <div class="progress-fill" :style="{ width: runProgress.pct + '%' }"></div>
        </div>
        <!-- Iteration indicator -->
        <div v-if="runProgress.iteration" class="text-[10px] text-zinc-600 font-mono mt-1">
          Iteration {{ runProgress.iteration }}/{{ runProgress.totalIterations || config.maxIterations }}
        </div>
      </div>

      <!-- Live variant rankings -->
      <div v-if="variants.length > 0" class="card rounded-md overflow-hidden mb-6">
        <div class="px-5 py-3 flex items-center justify-between" style="border-bottom:1px solid var(--border-subtle);">
          <span class="section-label">Live Rankings</span>
          <span class="text-[10px] text-zinc-600 font-body">{{ variants.length }} variants evaluated</span>
        </div>
        <div style="max-height:300px;overflow-y:auto;">
          <div
            v-for="(v, i) in sortedVariants"
            :key="v.id || i"
            class="px-5 py-3 flex items-start gap-3"
            :class="{ 'bg-lime-400/[0.02]': i === 0 }"
            style="border-bottom:1px solid var(--border-subtle);"
          >
            <span class="text-xs font-mono text-zinc-600 w-5 flex-shrink-0">{{ i + 1 }}.</span>
            <div class="flex-1 min-w-0">
              <div class="text-xs font-body text-zinc-300 truncate">{{ v.prompt_text || v.prompt }}</div>
              <div class="text-[10px] text-zinc-600 font-body mt-0.5">
                Iteration {{ v.iteration || 0 }}
              </div>
            </div>
            <span class="text-xs font-mono font-bold flex-shrink-0" :style="{ color: scoreColor(v.score * 100) }">
              {{ (v.score * 100).toFixed(1) }}%
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- Run complete -->
    <div v-else-if="runComplete">
      <!-- Best prompt -->
      <div v-if="bestVariant" class="card rounded-md p-5 mb-6" style="border-left:3px solid var(--lime);">
        <div class="flex items-center justify-between mb-3">
          <span class="section-label">Best Prompt</span>
          <span class="text-sm font-mono font-bold" style="color:var(--lime);">
            {{ (bestVariant.score * 100).toFixed(1) }}%
          </span>
        </div>
        <div
          class="text-xs text-zinc-400 font-body rounded-sm px-3 py-2 mb-3 cursor-pointer"
          style="background:rgba(0,0,0,0.2);border:1px solid var(--border-subtle);"
          :style="{ maxHeight: bestExpanded ? 'none' : '80px', overflow: bestExpanded ? 'visible' : 'hidden' }"
          @click="bestExpanded = !bestExpanded"
        >{{ bestVariant.prompt_text || bestVariant.prompt }}</div>

        <div class="flex items-center gap-2 flex-wrap">
          <button
            @click="applyBest"
            class="text-[10px] font-display tracking-wider uppercase px-3 py-1 rounded-sm"
            style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
          >Use This Prompt</button>
          <button
            v-if="!savedToLibrary"
            @click="saveToLibrary"
            class="text-[10px] font-display tracking-wider uppercase px-3 py-1 rounded-sm"
            style="background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.2);color:#38BDF8;"
          >Save to Library</button>
          <router-link
            v-else
            :to="{ name: 'PromptLibrary' }"
            class="text-[10px] font-display tracking-wider uppercase px-3 py-1 rounded-sm"
            style="background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.2);color:#38BDF8;"
          >View in Library →</router-link>
          <button
            @click="resetView"
            class="text-[10px] font-display tracking-wider uppercase px-3 py-1 rounded-sm"
            style="border:1px solid var(--border-subtle);color:#71717A;"
          >New Run</button>
        </div>
      </div>

      <!-- All variants ranked -->
      <div v-if="variants.length > 0" class="card rounded-md overflow-hidden">
        <div class="px-5 py-3 flex items-center justify-between" style="border-bottom:1px solid var(--border-subtle);">
          <span class="section-label">All Variants</span>
          <span class="text-[10px] text-zinc-600 font-body">{{ variants.length }} generated</span>
        </div>
        <div style="max-height:400px;overflow-y:auto;">
          <div
            v-for="(v, i) in sortedVariants"
            :key="v.id || i"
            class="px-5 py-3 flex items-start gap-3 hover:bg-white/[0.02] transition-colors"
            style="border-bottom:1px solid var(--border-subtle);"
          >
            <span class="text-xs font-mono text-zinc-600 w-5 flex-shrink-0">{{ i + 1 }}.</span>
            <div class="flex-1 min-w-0">
              <div class="text-xs font-body text-zinc-300">{{ v.prompt_text || v.prompt }}</div>
              <div class="text-[10px] text-zinc-600 font-body mt-0.5">
                Iteration {{ v.iteration || 0 }}
                <span v-if="v.source" class="ml-2">
                  <span class="px-1 py-0.5 rounded-sm" style="background:rgba(168,85,247,0.08);color:#A855F7;">{{ v.source }}</span>
                </span>
              </div>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0">
              <span class="text-xs font-mono font-bold" :style="{ color: scoreColor(v.score * 100) }">
                {{ (v.score * 100).toFixed(1) }}%
              </span>
              <button
                @click="applyVariant(v)"
                class="text-[9px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm"
                style="background:rgba(191,255,0,0.06);border:1px solid rgba(191,255,0,0.15);color:var(--lime);"
              >Use</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useToolEvalStore } from '../../stores/toolEval.js'
import { usePromptLibraryStore } from '../../stores/promptLibrary.js'
import { useNotificationsStore } from '../../stores/notifications.js'
import { useConfigStore } from '../../stores/config.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import { useToast } from '../../composables/useToast.js'
import { apiFetch } from '../../utils/api.js'

const teStore = useToolEvalStore()
const libraryStore = usePromptLibraryStore()
const notifStore = useNotificationsStore()
const configStore = useConfigStore()
const { setSystemPrompt, setConfig } = useSharedContext()
const { showToast } = useToast()

// --- Config state ---
const config = ref({
  suiteId: '',
  basePrompt: 'You are a helpful assistant that uses tools to answer questions. When the user asks you to perform an action, use the appropriate tool.',
  optimizationModelKey: '',
  maxIterations: 5,
  populationSize: 5,
})

// --- Run state ---
const isRunning = ref(false)
const runComplete = ref(false)
const starting = ref(false)
const startError = ref('')
const activeJobId = ref(null)
const variants = ref([])
const bestVariant = ref(null)
const bestExpanded = ref(false)
const savedToLibrary = ref(false)
const runProgress = ref({ pct: 0, detail: '', eta: '', iteration: null, totalIterations: null })

const allModels = computed(() => configStore.allModels || [])

const canStart = computed(() => {
  return config.value.suiteId && config.value.optimizationModelKey && !isRunning.value
})

const sortedVariants = computed(() => {
  return [...variants.value].sort((a, b) => (b.score || 0) - (a.score || 0))
})

let unsubscribe = null

onMounted(async () => {
  if (teStore.suites.length === 0) {
    try { await teStore.loadSuites() } catch { /* ignore */ }
  }
  try {
    await configStore.loadConfig()
  } catch { /* ignore */ }

  // Subscribe to WS messages
  unsubscribe = notifStore.onMessage((msg) => {
    if (!activeJobId.value) return
    if (msg.job_id && msg.job_id !== activeJobId.value) return
    handleWsMessage(msg)
  })
})

onUnmounted(() => {
  if (unsubscribe) unsubscribe()
})

function handleWsMessage(msg) {
  switch (msg.type) {
    case 'auto_optimize_start':
    case 'tune_start':
      isRunning.value = true
      runProgress.value = { pct: 0, detail: 'Starting optimization...', eta: '', iteration: null, totalIterations: config.value.maxIterations }
      break

    case 'prompt_generated':
    case 'auto_optimize_variant':
      if (msg.variant || msg.prompt) {
        const v = msg.variant || { prompt_text: msg.prompt, score: msg.score || 0, iteration: msg.iteration || 0, source: msg.source }
        variants.value = [...variants.value, v]
      }
      break

    case 'auto_optimize_progress':
    case 'job_progress':
      runProgress.value = {
        pct: msg.progress_pct ?? runProgress.value.pct,
        detail: msg.progress_detail || msg.detail || runProgress.value.detail,
        eta: runProgress.value.eta,
        iteration: msg.iteration || runProgress.value.iteration,
        totalIterations: msg.total_iterations || runProgress.value.totalIterations,
      }
      break

    case 'auto_optimize_complete':
    case 'tune_complete':
    case 'job_completed': {
      isRunning.value = false
      runComplete.value = true
      runProgress.value = { ...runProgress.value, pct: 100, detail: 'Complete!' }
      // Set best variant from message or derive from variants
      if (msg.best_prompt) {
        bestVariant.value = { prompt_text: msg.best_prompt, score: msg.best_score || 0 }
      } else if (sortedVariants.value.length > 0) {
        bestVariant.value = sortedVariants.value[0]
      }
      if (msg.type === 'auto_optimize_complete' || msg.type === 'tune_complete') {
        showToast('Auto-optimize complete!', 'success')
        autoSaveBest()
      }
      break
    }

    case 'job_failed':
      isRunning.value = false
      showToast(msg.error || 'Auto-optimize failed', 'error')
      break

    case 'job_cancelled':
      isRunning.value = false
      showToast('Run cancelled', '')
      break
  }
}

async function startAutoOptimize() {
  if (!canStart.value || starting.value) return
  starting.value = true
  startError.value = ''
  variants.value = []
  bestVariant.value = null
  savedToLibrary.value = false

  const idx = config.value.optimizationModelKey.indexOf('::')
  const providerKey = config.value.optimizationModelKey.substring(0, idx)
  const modelId = config.value.optimizationModelKey.substring(idx + 2)

  const body = {
    suite_id: config.value.suiteId,
    base_prompt: config.value.basePrompt,
    optimization_model: modelId,
    optimization_provider_key: providerKey,
    max_iterations: config.value.maxIterations,
    population_size: config.value.populationSize,
  }

  try {
    const res = await apiFetch('/api/tool-eval/prompt-tune/auto-optimize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || err.error || `Error ${res.status}`)
    }
    const data = await res.json()
    activeJobId.value = data.job_id
    isRunning.value = true
    showToast('Auto-optimize started', 'success')
  } catch (e) {
    startError.value = e.message || 'Failed to start'
  } finally {
    starting.value = false
  }
}

async function cancelRun() {
  if (!activeJobId.value) return
  try {
    await apiFetch('/api/jobs/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: activeJobId.value }),
    })
  } catch {
    showToast('Failed to cancel', 'error')
  }
}

function applyBest() {
  if (!bestVariant.value) return
  const prompt = bestVariant.value.prompt_text || bestVariant.value.prompt
  setSystemPrompt('_global', prompt)
  setConfig({ lastUpdatedBy: 'auto_optimize' })
  showToast('Best prompt applied to shared context', 'success')
}

function applyVariant(v) {
  const prompt = v.prompt_text || v.prompt
  setSystemPrompt('_global', prompt)
  setConfig({ lastUpdatedBy: 'auto_optimize' })
  showToast('Prompt applied to shared context', 'success')
}

async function saveToLibrary() {
  if (!bestVariant.value) return
  const prompt = bestVariant.value.prompt_text || bestVariant.value.prompt
  try {
    await libraryStore.saveVersion(prompt, null, 'auto_optimize')
    savedToLibrary.value = true
    showToast('Saved to Prompt Library', 'success')
  } catch {
    showToast('Failed to save to library', 'error')
  }
}

async function autoSaveBest() {
  if (!bestVariant.value) return
  try {
    const prompt = bestVariant.value.prompt_text || bestVariant.value.prompt
    await libraryStore.saveVersion(prompt, null, 'auto_optimize')
    savedToLibrary.value = true
  } catch { /* non-fatal */ }
}

function viewActiveRun() {
  // scroll down to run view (it's already visible)
}

function resetView() {
  isRunning.value = false
  runComplete.value = false
  variants.value = []
  bestVariant.value = null
  savedToLibrary.value = false
  activeJobId.value = null
  runProgress.value = { pct: 0, detail: '', eta: '', iteration: null, totalIterations: null }
}

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
}
</script>
