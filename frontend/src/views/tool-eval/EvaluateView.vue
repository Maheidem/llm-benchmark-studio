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
              @click.stop="toggleModel(m.key)"
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

    <!-- Profile Picker (shown when models are selected and profiles exist) -->
    <div v-if="selectedModelsList.length > 0 && profilesStore.profiles.length > 0" class="card rounded-md p-5 mb-6">
      <span class="section-label block mb-3">Profiles (optional)</span>
      <div class="flex flex-col gap-2">
        <div v-for="m in selectedModelsList" :key="m.id" class="flex items-center gap-3">
          <span class="text-xs font-mono text-zinc-400 w-40 truncate" :title="m.model_id">{{ m.display_name }}</span>
          <select
            v-model="selectedProfiles[m.model_id]"
            class="text-xs font-mono px-2 py-1 rounded-sm flex-1"
            style="background:var(--surface);border:1px solid var(--border-subtle);color:var(--zinc-200);outline:none;"
          >
            <option value="">No Profile</option>
            <option
              v-for="p in (profilesStore.profilesByModel[m.model_id] || [])"
              :key="p.id"
              :value="p.id"
            >{{ p.name }}{{ p.is_default ? ' (default)' : '' }}</option>
          </select>
        </div>
      </div>
    </div>

    <!-- Eval Settings -->
    <div class="card rounded-md p-5 mb-6">
      <div class="flex items-center justify-between mb-4">
        <span class="section-label">Eval Settings</span>
      </div>
      <div class="flex items-center gap-4 flex-wrap">
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
        <label class="flex items-center gap-2 cursor-pointer" title="Automatically run judge analysis after eval completes">
          <input type="checkbox" v-model="autoJudge" class="accent-amber-400">
          <span class="text-xs font-body text-zinc-400">Auto-run Judge</span>
        </label>
        <div v-if="autoJudge" class="flex items-center gap-2" title="Trigger judge when a model's overall score falls below this threshold">
          <span class="text-[10px] text-zinc-600 font-body">on score &lt;</span>
          <input
            v-model.number="autoJudgeThreshold"
            type="range"
            min="0"
            max="100"
            step="5"
            class="w-20 accent-amber-400"
          >
          <span class="text-[10px] font-mono text-amber-400 w-8">{{ autoJudgeThreshold }}%</span>
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

    <!-- Irrelevance + tool_choice=required warning -->
    <div
      v-if="showIrrelevanceWarning"
      class="mb-6 px-4 py-3 rounded-sm flex items-start gap-3"
      style="background:rgba(251,191,36,0.06);border:1px solid rgba(251,191,36,0.25);"
    >
      <svg class="w-4 h-4 flex-shrink-0 mt-0.5" fill="none" stroke="#FBBF24" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
      </svg>
      <div>
        <span class="text-xs font-display tracking-wider uppercase text-amber-400">Warning</span>
        <p class="text-xs text-zinc-400 font-body mt-0.5">
          This suite contains irrelevance test cases but <strong class="text-zinc-200">Tool Choice is set to "Required"</strong>.
          The model will be forced to call a tool, making all irrelevance cases fail artificially.
          Switch Tool Choice to <button
            @click="toolChoice = 'auto'"
            class="text-amber-400 underline hover:text-amber-300 font-mono"
          >"auto"</button> to test abstention correctly.
        </p>
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
            <tr v-for="(r, i) in store.evalResults" :key="i" @click="showDetail(r.model_id)" class="cursor-pointer hover:bg-white/[0.02]">
              <td class="px-5 py-2 text-xs font-mono text-zinc-300">{{ r.model_name || r.model_id || '' }}</td>
              <td class="px-5 py-2 text-xs font-body text-zinc-400">
                {{ truncate(r.prompt || r.test_case_id || '', 40) }}
                <span
                  v-if="r.should_call_tool === false"
                  class="text-[9px] font-display tracking-wider uppercase px-1 py-0.5 rounded-sm ml-1 align-middle"
                  style="color:#38BDF8;background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.15)"
                >IRREL</span>
              </td>
              <td class="px-5 py-2 text-xs font-mono text-zinc-500">
                {{ r.should_call_tool === false ? '(abstain)' : formatTool(r.expected_tool) }}
              </td>
              <td class="px-5 py-2 text-xs font-mono" :style="{ color: liveActualColor(r) }">
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

    <!-- Auto-judge completion banner -->
    <div
      v-if="judgeReportReady"
      class="mt-4 px-4 py-3 rounded-sm flex items-center justify-between"
      style="background:rgba(251,191,36,0.06);border:1px solid rgba(251,191,36,0.25);"
    >
      <div class="flex items-center gap-2">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="#FBBF24" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <span class="text-xs text-zinc-400 font-body">Judge analysis complete</span>
      </div>
      <router-link
        :to="{ name: 'JudgeHistory' }"
        class="text-[10px] font-display tracking-wider uppercase px-3 py-1 rounded-sm"
        style="color:#FBBF24;background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.2);"
      >View Report →</router-link>
    </div>

    <!-- Model Detail Modal -->
    <ModelDetailModal
      :visible="detailModalVisible"
      :model-id="detailModelId"
      :all-results="store.evalResults"
      :eval-id="store.lastEvalId"
      @close="detailModalVisible = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useToolEvalStore } from '../../stores/toolEval.js'
import { useProfilesStore } from '../../stores/profiles.js'
import { useNotificationsStore } from '../../stores/notifications.js'
import { useToast } from '../../composables/useToast.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import { apiFetch } from '../../utils/api.js'
import { getColor } from '../../utils/constants.js'
import SystemPromptEditor from '../../components/tool-eval/SystemPromptEditor.vue'
import EvalResultsTable from '../../components/tool-eval/EvalResultsTable.vue'
import ModelDetailModal from '../../components/tool-eval/ModelDetailModal.vue'

const store = useToolEvalStore()
const profilesStore = useProfilesStore()
const notifStore = useNotificationsStore()
const { showToast } = useToast()
const { context, setSuite, setModels, setConfig } = useSharedContext()

// --- State ---
const selectedSuiteId = ref('')
const selectedModels = ref(new Set())
const temperature = ref(0.0)
const toolChoice = ref('required')
const autoJudge = ref(false)
const autoJudgeThreshold = ref(80)
const systemPrompts = ref({})
const loadingConfig = ref(true)
const providerGroups = ref([])
const progressLabel = ref('Initializing...')
const progressCount = ref('0/0')
const progressPct = ref(0)
const errorBanner = ref('')

const detailModalVisible = ref(false)
const detailModelId = ref('')
const judgeReportReady = ref(false)

// Profile picker: map of model_id -> selected profile id ('' = no profile)
const selectedProfiles = ref({})

const selectedModelsList = computed(() => {
  return Array.from(selectedModels.value)
    .filter(key => key && key.includes('::'))
    .map(key => {
      const idx = key.indexOf('::')
      const modelId = key.substring(idx + 2)
      return {
        id: key,
        model_id: modelId,
        display_name: modelId.split('/').pop() || modelId,
      }
    })
})

// --- Load Config ---
onMounted(async () => {
  // Load suites if not loaded
  if (store.suites.length === 0) {
    try { await store.loadSuites() } catch { showToast('Failed to load suites', 'error') }
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
    selectedModels.value = new Set(context.selectedModels)
  }
  if (context.temperature != null) temperature.value = context.temperature
  if (context.toolChoice) toolChoice.value = context.toolChoice
  if (context.systemPrompts) systemPrompts.value = { ...context.systemPrompts }

  // Load profiles
  try { await profilesStore.fetchProfiles() } catch { showToast('Failed to load model profiles', 'error') }

  // Load judge settings to get default auto_judge value
  try {
    const jRes = await apiFetch('/api/settings/judge')
    if (jRes.ok) {
      const jSettings = await jRes.json()
      autoJudge.value = !!jSettings.auto_judge_after_eval
    }
  } catch { /* non-fatal */ }

  // Restore running eval
  const storedJobId = sessionStorage.getItem('_teJobId')
  if (storedJobId) {
    store.activeJobId = storedJobId
    store.isEvaluating = true
  }

  // Subscribe to eval events via global WS (same pattern as ParamTunerRun)
  unsubscribe = notifStore.onMessage(handleWsMessage)
})

let unsubscribe = null

onUnmounted(() => {
  if (unsubscribe) unsubscribe()
})

function buildProviderGroups(config) {
  const groups = []
  const providers = config.providers || {}

  for (const [provKey, prov] of Object.entries(providers)) {
    const pk = prov.provider_key || provKey
    const models = (prov.models || []).map(m => ({
      key: `${pk}::${m.model_id}`,
      model_id: m.model_id,
      display_name: m.display_name || m.model_id,
      provider_key: pk,
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
  const s = new Set(selectedModels.value)
  if (s.has(key)) s.delete(key)
  else s.add(key)
  selectedModels.value = s
  setModels(Array.from(s))
}

function allProviderSelected(group) {
  return group.models.every(m => selectedModels.value.has(m.key))
}

function toggleProvider(group) {
  const s = new Set(selectedModels.value)
  const allSel = group.models.every(m => s.has(m.key))
  for (const m of group.models) {
    if (allSel) s.delete(m.key)
    else s.add(m.key)
  }
  selectedModels.value = s
  setModels(Array.from(s))
}

// --- Suite change ---

async function onSuiteChange() {
  const suite = store.suites.find(s => s.id === selectedSuiteId.value)
  if (suite) {
    setSuite(suite.id, suite.name)
    // Load full suite data (including test_cases) for irrelevance warning
    try { await store.loadSuite(selectedSuiteId.value) } catch { /* non-fatal */ }
  }
}

// Warn when tool_choice=required AND suite has irrelevance test cases
const showIrrelevanceWarning = computed(() => {
  if (toolChoice.value !== 'required') return false
  const testCases = store.currentSuite?.test_cases
  if (!testCases) return false
  return testCases.some(c => c.should_call_tool === false)
})

// --- WebSocket message handler (receives messages via notifStore.onMessage) ---

function handleWsMessage(msg) {
  // Handle judge_complete regardless of job_id (auto-judge runs as separate job)
  if (msg.type === 'judge_complete') {
    judgeReportReady.value = true
    showToast('Judge report ready — view in Judge History', 'success')
    return
  }

  // Handle auto_judge_skipped outside the job_id guard — arrives after job_completed clears activeJobId
  if (msg.type === 'auto_judge_skipped') {
    if (msg.reason === 'no_judge_model') {
      showToast('Auto-judge skipped: no judge model configured. Set one in Settings > Judge.', 'error')
    } else if (msg.reason === 'score_above_threshold') {
      showToast(msg.detail || 'Auto-judge skipped: scores above threshold', '')
    } else if (msg.reason === 'submission_failed') {
      showToast(msg.detail || 'Auto-judge failed to start', 'error')
    }
    return
  }

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
    case 'eval_warning':
      showToast(msg.detail || 'Warning during evaluation', '')
      break
    case 'param_adjustments':
      if (msg.models) {
        for (const m of msg.models) {
          const descs = (m.adjustments || []).map(a =>
            a.action === 'drop' ? `${a.param} dropped` :
            a.action === 'clamp' ? `${a.param} clamped ${a.original}→${a.adjusted}` :
            a.action === 'rename' ? `${a.param} renamed` :
            `${a.param} ${a.action}`
          )
          if (descs.length) showToast(`${m.model_id}: ${descs.join(', ')}`, '')
        }
      }
      break
    case 'judge_failed':
      showToast(msg.detail || 'Judge analysis failed', 'error')
      break
    case 'job_cancelled':
      progressLabel.value = 'Cancelled'
      showToast('Eval cancelled', '')
      break
  }
}

// --- Eval actions ---

async function startEval() {
  if (selectedModels.value.size === 0) {
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
  judgeReportReady.value = false

  // Build request body
  const targets = Array.from(selectedModels.value).map(k => {
    const i = k.indexOf('::')
    return { provider_key: k.substring(0, i), model_id: k.substring(i + 2) }
  })

  const body = {
    suite_id: selectedSuiteId.value,
    targets,
    temperature: temperature.value,
    tool_choice: toolChoice.value,
    auto_judge: autoJudge.value,
    auto_judge_threshold: autoJudge.value ? autoJudgeThreshold.value / 100 : null,
  }

  // Provider params from shared context (set by param tuner or manual config)
  if (context.providerParams && Object.keys(context.providerParams).length > 0) {
    body.provider_params = context.providerParams
  }

  // System prompts
  const spDict = {}
  for (const [k, v] of Object.entries(systemPrompts.value)) {
    if (v && v.trim()) spDict[k] = v
  }
  if (Object.keys(spDict).length > 0) {
    body.system_prompt = spDict
  }

  // Profiles — only include models that have a profile selected
  const profilesMap = {}
  for (const [modelId, profileId] of Object.entries(selectedProfiles.value)) {
    if (profileId) profilesMap[modelId] = profileId
  }
  if (Object.keys(profilesMap).length > 0) {
    body.profiles = profilesMap
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

// For irrelevance cases: (none) actual tool = PASS (lime), any tool called = FAIL (coral)
// For normal cases: use tool_selection_score as before
function liveActualColor(r) {
  if (r.should_call_tool === false) {
    return r.actual_tool ? 'var(--coral)' : 'var(--lime)'
  }
  return r.tool_selection_score > 0 ? 'var(--lime)' : 'var(--coral)'
}

function showDetail(modelId) {
  detailModelId.value = modelId
  detailModalVisible.value = true
}
</script>
