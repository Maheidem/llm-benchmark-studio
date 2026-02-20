<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Evaluate</h2>
        <p class="text-sm text-zinc-600 font-body">Select models, configure settings, and run tool calling evaluations.</p>
      </div>
    </div>

    <!-- Suite Selector -->
    <div class="card rounded-md p-5 mb-6">
      <label class="section-label mb-2 block">Suite</label>
      <select
        v-model="selectedSuiteId"
        @change="onSuiteChange"
        class="text-sm font-mono px-3 py-2 rounded-sm w-full"
        style="background:var(--surface);border:1px solid var(--border-subtle);color:#E4E4E7;outline:none;"
      >
        <option value="">-- Select a suite --</option>
        <option v-for="s in store.suites" :key="s.id" :value="s.id">
          {{ s.name }} ({{ s.tool_count || 0 }} tools, {{ s.test_case_count || 0 }} cases)
        </option>
      </select>
    </div>

    <!-- Model Selection Grid -->
    <div class="card rounded-md p-5 mb-6">
      <div class="flex items-center justify-between mb-3">
        <span class="section-label">Models</span>
        <span class="text-xs font-mono text-zinc-600">{{ selectedModels.size }} selected</span>
      </div>

      <div v-if="loadingConfig" class="text-xs text-zinc-600 font-body">Loading models...</div>

      <div v-else-if="providerGroups.length === 0" class="text-xs text-zinc-600 font-body">
        No models available. Configure providers in Settings.
      </div>

      <div v-else class="flex flex-col gap-1">
        <div v-for="group in providerGroups" :key="group.provider" class="provider-group">
          <div
            class="provider-group-header"
            @click="group.collapsed = !group.collapsed"
          >
            <div class="provider-group-dot" :style="{ background: group.color }"></div>
            <span class="provider-group-label" :style="{ color: group.color }">{{ group.provider }}</span>
            <span class="provider-group-count">{{ group.models.length }}</span>
            <button
              @click.stop="toggleProvider(group)"
              class="text-[10px] font-display tracking-wider uppercase text-zinc-600 hover:text-zinc-400 ml-2"
            >{{ allProviderSelected(group) ? 'None' : 'All' }}</button>
          </div>
          <div v-show="!group.collapsed" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 pl-4 pb-2">
            <div
              v-for="m in group.models"
              :key="m.key"
              class="model-card rounded-sm px-3 py-2 flex items-center gap-2"
              :class="{ selected: selectedModels.has(m.key) }"
              @click="toggleModel(m.key)"
            >
              <div class="check-dot"></div>
              <div class="flex-1 min-w-0">
                <div class="text-xs font-mono text-zinc-200 truncate">{{ m.display_name || m.model_id }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Eval Settings -->
    <div class="card rounded-md p-5 mb-6">
      <div class="flex items-center justify-between mb-4">
        <span class="section-label">Eval Settings</span>
      </div>
      <div class="flex items-center gap-4">
        <div>
          <span class="text-zinc-500 font-body text-xs">Temperature</span>
          <input
            v-model.number="temperature"
            type="number"
            min="0"
            max="2"
            step="0.1"
            class="ml-2 w-16 px-2 py-1 rounded-sm text-sm font-mono text-zinc-200 text-center"
            style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
          >
        </div>
        <div>
          <label class="text-xs font-display tracking-wider text-zinc-500 uppercase">Tool Choice</label>
          <select
            v-model="toolChoice"
            class="text-xs font-mono px-3 py-1.5 rounded-sm ml-2"
            style="background:var(--surface);border:1px solid var(--border-subtle);color:var(--zinc-200);width:120px"
          >
            <option value="required">Required</option>
            <option value="auto">Auto</option>
            <option value="none">None</option>
          </select>
        </div>
        <div class="ml-auto flex gap-2">
          <button
            v-if="store.isEvaluating"
            @click="cancelEval"
            class="text-[11px] font-display font-medium tracking-wider uppercase px-3 py-1 rounded-sm transition-colors"
            style="color:var(--coral);border:1px solid rgba(255,59,92,0.3);"
          >Cancel</button>
          <button
            @click="startEval"
            :disabled="store.isEvaluating || selectedModels.size === 0 || !selectedSuiteId"
            class="run-btn px-6 py-2 rounded-sm text-xs flex items-center gap-2"
          >
            <svg v-if="store.isEvaluating" class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
            <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
            {{ store.isEvaluating ? 'Running...' : 'Start Eval' }}
          </button>
        </div>
      </div>
    </div>

    <!-- System Prompt -->
    <SystemPromptEditor
      v-if="selectedModels.size > 0"
      :models="selectedModelsList"
      :system-prompts="systemPrompts"
      @update:system-prompts="systemPrompts = $event"
    />

    <!-- Progress Section -->
    <div v-if="store.isEvaluating" class="mb-6 fade-in">
      <div class="card rounded-md p-5">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-3">
            <div class="pulse-dot"></div>
            <span class="text-sm text-zinc-400 font-body">{{ progressLabel }}</span>
          </div>
          <span class="text-xs font-mono text-zinc-600">{{ progressCount }}</span>
        </div>
        <div class="progress-track rounded-full overflow-hidden">
          <div class="progress-fill" :style="{ width: progressPct + '%' }"></div>
        </div>
      </div>
      <div v-if="errorBanner" class="error-banner mt-3">
        <span>{{ errorBanner }}</span>
      </div>
    </div>

    <!-- Live Results Table -->
    <div v-if="store.evalResults.length > 0" class="card rounded-md overflow-hidden mb-6">
      <div class="px-5 py-3" style="border-bottom:1px solid var(--border-subtle);">
        <span class="section-label">Live Results</span>
      </div>
      <div style="max-height:400px;overflow-y:auto;">
        <table class="w-full text-sm results-table">
          <thead>
            <tr style="border-bottom:1px solid var(--border-subtle)">
              <th class="px-5 py-2 text-left section-label">Model</th>
              <th class="px-5 py-2 text-left section-label">Prompt</th>
              <th class="px-5 py-2 text-left section-label">Expected</th>
              <th class="px-5 py-2 text-left section-label">Actual</th>
              <th class="px-5 py-2 text-right section-label">Hops</th>
              <th class="px-5 py-2 text-right section-label">Score</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in store.evalResults" :key="i">
              <td class="px-5 py-2 text-xs font-mono text-zinc-300">{{ r.model_name || r.model_id || '' }}</td>
              <td class="px-5 py-2 text-xs font-body text-zinc-400">{{ truncate(r.prompt || r.test_case_id || '', 40) }}</td>
              <td class="px-5 py-2 text-xs font-mono text-zinc-500">{{ formatTool(r.expected_tool) }}</td>
              <td class="px-5 py-2 text-xs font-mono" :style="{ color: r.tool_selection_score > 0 ? 'var(--lime)' : 'var(--coral)' }">
                {{ r.actual_tool || '(none)' }}
              </td>
              <td class="px-5 py-2 text-right text-xs font-mono text-zinc-500" :title="r.tool_chain ? r.tool_chain.map(c => c.tool_name).join(' \u2192 ') : ''">
                {{ r.multi_turn ? (r.tool_chain?.length || 1) : '' }}
              </td>
              <td class="px-5 py-2 text-right text-xs font-mono font-bold" :style="{ color: scoreColor(r.overall_score * 100) }">
                {{ (r.overall_score * 100).toFixed(0) }}%
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Summary Results -->
    <EvalResultsTable
      v-if="store.evalSummaries.length > 0"
      :results="store.evalSummaries"
      @show-detail="showDetail"
    />

    <!-- Model Detail Modal -->
    <ModelDetailModal
      :visible="detailModalVisible"
      :model-id="detailModelId"
      :all-results="store.evalResults"
      @close="detailModalVisible = false"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useToolEvalStore } from '../../stores/toolEval.js'
import { useToast } from '../../composables/useToast.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import { useWebSocket } from '../../composables/useWebSocket.js'
import { apiFetch, getToken } from '../../utils/api.js'
import { getColor } from '../../utils/constants.js'
import SystemPromptEditor from '../../components/tool-eval/SystemPromptEditor.vue'
import EvalResultsTable from '../../components/tool-eval/EvalResultsTable.vue'
import ModelDetailModal from '../../components/tool-eval/ModelDetailModal.vue'

const store = useToolEvalStore()
const { showToast } = useToast()
const { context, setSuite, setModels, setConfig } = useSharedContext()

// --- State ---
const selectedSuiteId = ref('')
const selectedModels = reactive(new Set())
const temperature = ref(0.0)
const toolChoice = ref('required')
const systemPrompts = ref({})
const loadingConfig = ref(true)
const providerGroups = ref([])
const progressLabel = ref('Initializing...')
const progressCount = ref('0/0')
const progressPct = ref(0)
const errorBanner = ref('')

const detailModalVisible = ref(false)
const detailModelId = ref('')

const selectedModelsList = computed(() => {
  return Array.from(selectedModels).map(key => {
    const idx = key.indexOf('::')
    return {
      id: key,
      model_id: key.substring(idx + 2),
      display_name: key.substring(idx + 2).split('/').pop(),
    }
  })
})

// --- Load Config ---
onMounted(async () => {
  // Load suites if not loaded
  if (store.suites.length === 0) {
    try { await store.loadSuites() } catch { /* ignore */ }
  }

  // Load model config
  try {
    const res = await apiFetch('/api/config')
    if (res.ok) {
      const config = await res.json()
      buildProviderGroups(config)
    }
  } catch {
    showToast('Failed to load model config', 'error')
  } finally {
    loadingConfig.value = false
  }

  // Restore context
  if (context.suiteId) selectedSuiteId.value = context.suiteId
  if (context.selectedModels?.length) {
    context.selectedModels.forEach(m => selectedModels.add(m))
  }
  if (context.temperature != null) temperature.value = context.temperature
  if (context.toolChoice) toolChoice.value = context.toolChoice
  if (context.systemPrompts) systemPrompts.value = { ...context.systemPrompts }

  // Restore running eval
  const storedJobId = sessionStorage.getItem('_teJobId')
  if (storedJobId) {
    store.activeJobId = storedJobId
    store.isEvaluating = true
  }

  connectWebSocket()
})

function buildProviderGroups(config) {
  const groups = []
  const providers = config.providers || {}

  for (const [provKey, prov] of Object.entries(providers)) {
    const models = (prov.models || []).map(m => ({
      key: `${provKey}::${m.id}`,
      model_id: m.id,
      display_name: m.display_name || m.id,
      provider_key: provKey,
    }))
    if (models.length === 0) continue

    const color = getColor(prov.display_name || provKey)
    groups.push({
      provider: prov.display_name || provKey,
      color: color.text,
      models,
      collapsed: false,
    })
  }

  providerGroups.value = groups
}

// --- Model selection ---

function toggleModel(key) {
  if (selectedModels.has(key)) {
    selectedModels.delete(key)
  } else {
    selectedModels.add(key)
  }
  setModels(Array.from(selectedModels))
}

function allProviderSelected(group) {
  return group.models.every(m => selectedModels.has(m.key))
}

function toggleProvider(group) {
  const allSel = allProviderSelected(group)
  for (const m of group.models) {
    if (allSel) {
      selectedModels.delete(m.key)
    } else {
      selectedModels.add(m.key)
    }
  }
  setModels(Array.from(selectedModels))
}

// --- Suite change ---

function onSuiteChange() {
  const suite = store.suites.find(s => s.id === selectedSuiteId.value)
  if (suite) {
    setSuite(suite.id, suite.name)
  }
}

// --- WebSocket ---

function connectWebSocket() {
  const token = getToken()
  if (!token) return

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${proto}//${location.host}/ws?token=${token}`

  const { connect } = useWebSocket(wsUrl, {
    onMessage: handleWsMessage,
  })
  connect()
}

function handleWsMessage(msg) {
  if (!store.activeJobId) return
  if (msg.job_id !== store.activeJobId) return

  store.handleEvalProgress(msg)

  // UI updates
  switch (msg.type) {
    case 'tool_eval_init': {
      const data = msg.data || msg
      progressLabel.value = `Evaluating ${data.suite_name || ''}...`
      progressCount.value = `0/${store.evalTotalCases}`
      break
    }
    case 'tool_eval_progress': {
      const data = msg.data || msg
      const pct = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0
      progressPct.value = pct
      progressCount.value = `${data.current}/${data.total}`
      progressLabel.value = `Testing ${data.model || ''}...`
      // ETA
      if (store.evalStartTime && pct > 0 && pct < 100) {
        const elapsed = (Date.now() - store.evalStartTime) / 1000
        const remaining = (elapsed / pct) * (100 - pct)
        const etaText = remaining > 60 ? Math.ceil(remaining / 60) + 'm left' : Math.ceil(remaining) + 's left'
        progressLabel.value += ' \u2014 ' + etaText
      }
      break
    }
    case 'tool_eval_result': {
      // Update progress bar based on result count
      if (store.evalTotalCases > 0) {
        progressPct.value = Math.round((store.evalResults.length / store.evalTotalCases) * 100)
        progressCount.value = `${store.evalResults.length}/${store.evalTotalCases}`
      }
      break
    }
    case 'tool_eval_complete':
      progressLabel.value = 'Complete!'
      progressPct.value = 100
      showToast('Eval complete!', 'success')
      break
    case 'job_completed':
      progressLabel.value = 'Complete!'
      progressPct.value = 100
      break
    case 'job_failed':
      errorBanner.value = msg.error || msg.error_msg || 'Eval failed'
      break
    case 'job_cancelled':
      progressLabel.value = 'Cancelled'
      showToast('Eval cancelled', '')
      break
  }
}

// --- Eval actions ---

async function startEval() {
  if (selectedModels.size === 0) {
    showToast('Select at least one model', 'error')
    return
  }
  if (!selectedSuiteId.value) {
    showToast('No suite selected', 'error')
    return
  }

  store.resetEval()
  progressLabel.value = 'Initializing...'
  progressPct.value = 0
  progressCount.value = '0/0'
  errorBanner.value = ''

  // Build request body
  const targets = Array.from(selectedModels).map(k => {
    const i = k.indexOf('::')
    return { provider_key: k.substring(0, i), model_id: k.substring(i + 2) }
  })

  const body = {
    suite_id: selectedSuiteId.value,
    targets,
    temperature: temperature.value,
    tool_choice: toolChoice.value,
  }

  // System prompts
  const spDict = {}
  for (const [k, v] of Object.entries(systemPrompts.value)) {
    if (v && v.trim()) spDict[k] = v
  }
  if (Object.keys(spDict).length > 0) {
    body.system_prompt = spDict
  }

  // Experiment
  if (context.experimentId) {
    body.experiment_id = context.experimentId
  }

  // Save to context
  setConfig({
    temperature: temperature.value,
    toolChoice: toolChoice.value,
    systemPrompts: spDict,
    lastUpdatedBy: null,
  })

  try {
    await store.runEval(body)
    showToast('Eval submitted', 'success')
  } catch (e) {
    showToast(e.message || 'Failed to start eval', 'error')
    store.isEvaluating = false
  }
}

async function cancelEval() {
  try {
    await store.cancelEval()
    showToast('Cancellation requested', '')
  } catch {
    showToast('Failed to cancel', 'error')
  }
}

// --- Helpers ---

function truncate(s, max) {
  if (!s) return ''
  return s.length > max ? s.slice(0, max) + '...' : s
}

function formatTool(tool) {
  if (tool === null || tool === undefined) return '(none)'
  if (Array.isArray(tool)) return tool.join(' | ')
  if (typeof tool === 'object') return JSON.stringify(tool)
  return tool
}

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
}

function showDetail(modelId) {
  detailModelId.value = modelId
  detailModalVisible.value = true
}
</script>
