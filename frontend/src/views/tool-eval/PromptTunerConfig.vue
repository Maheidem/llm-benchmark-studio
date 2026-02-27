<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Prompt Tuner</h2>
        <p class="text-sm text-zinc-600 font-body">AI-powered system prompt optimization for tool calling.</p>
      </div>
      <div class="flex items-center gap-2">
        <router-link v-if="prtStore.isRunning" :to="{ name: 'PromptTunerRun' }"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
          style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
        >View Active Run</router-link>
        <router-link :to="{ name: 'PromptTunerHistory' }"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
          style="border:1px solid var(--border-subtle);color:var(--zinc-400);"
        >History</router-link>
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
        <option v-for="s in teStore.suites" :key="s.id" :value="s.id">
          {{ s.name }} ({{ s.tool_count || 0 }} tools, {{ s.test_case_count || 0 }} cases)
        </option>
      </select>
    </div>

    <!-- Mode Selection -->
    <div class="card rounded-md p-5 mb-6">
      <span class="section-label mb-3 block">Tuning Mode</span>
      <div class="grid grid-cols-2 gap-3">
        <div
          class="rounded-sm px-4 py-3 cursor-pointer transition-colors"
          :class="mode === 'quick' ? 'bg-white/[0.04]' : 'hover:bg-white/[0.02]'"
          :style="mode === 'quick' ? { border: '1px solid rgba(191,255,0,0.3)' } : { border: '1px solid var(--border-subtle)' }"
          @click="mode = 'quick'"
        >
          <div class="text-xs font-display font-bold text-zinc-200 mb-1">Quick</div>
          <div class="text-[10px] text-zinc-600 font-body">Generate N prompts, evaluate once. Fast and simple.</div>
        </div>
        <div
          class="rounded-sm px-4 py-3 cursor-pointer transition-colors"
          :class="mode === 'evolutionary' ? 'bg-white/[0.04]' : 'hover:bg-white/[0.02]'"
          :style="mode === 'evolutionary' ? { border: '1px solid rgba(168,85,247,0.3)' } : { border: '1px solid var(--border-subtle)' }"
          @click="mode = 'evolutionary'"
        >
          <div class="text-xs font-display font-bold text-zinc-200 mb-1">Evolutionary</div>
          <div class="text-[10px] text-zinc-600 font-body">Multiple generations with selection. Best survive and mutate.</div>
        </div>
      </div>
    </div>

    <!-- Meta Model Selection -->
    <div class="card rounded-md p-5 mb-6">
      <span class="section-label mb-3 block">Meta Model (generates prompts)</span>
      <select
        v-model="metaModelKey"
        class="text-sm font-mono px-3 py-2 rounded-sm w-full"
        style="background:var(--surface);border:1px solid var(--border-subtle);color:#E4E4E7;outline:none;"
      >
        <option value="">-- Select meta model --</option>
        <option v-for="m in allModels" :key="m.compoundKey" :value="m.compoundKey">
          {{ m.display_name || m.model_id }} ({{ m.provider }})
        </option>
      </select>
    </div>

    <!-- Target Models -->
    <div class="card rounded-md p-5 mb-6">
      <div class="flex items-center justify-between mb-3">
        <span class="section-label">Target Models (evaluated with generated prompts)</span>
        <span class="text-xs font-mono text-zinc-600">{{ selectedTargets.size }} selected</span>
      </div>

      <div v-if="loadingConfig" class="text-xs text-zinc-600 font-body">Loading models...</div>

      <div v-else class="flex flex-col gap-1">
        <div v-for="group in providerGroups" :key="group.provider" class="provider-group">
          <div class="provider-group-header" @click="group.collapsed = !group.collapsed">
            <div class="provider-group-dot" :style="{ background: group.color }"></div>
            <span class="provider-group-label" :style="{ color: group.color }">{{ group.provider }}</span>
            <span class="provider-group-count">{{ group.models.length }}</span>
          </div>
          <div v-show="!group.collapsed" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 pl-4 pb-2">
            <div
              v-for="m in group.models"
              :key="m.key"
              class="model-card rounded-sm px-3 py-2 flex items-center gap-2"
              :class="{ selected: selectedTargets.has(m.key) }"
              @click="toggleTarget(m.key)"
            >
              <div class="check-dot"></div>
              <div class="text-xs font-mono text-zinc-200 truncate">{{ m.display_name || m.model_id }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Base System Prompt -->
    <div class="card rounded-md p-5 mb-6">
      <div class="flex items-center justify-between mb-2">
        <span class="section-label">Base System Prompt</span>
        <div class="flex items-center gap-2">
          <button
            @click="saveCurrentToLibrary"
            :disabled="!basePrompt.trim()"
            class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm transition-opacity"
            :class="!basePrompt.trim() ? 'opacity-40 cursor-not-allowed' : ''"
            style="background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.2);color:#38BDF8;"
            title="Save current prompt to library"
          >Save to Library</button>
          <button
            @click="openLibraryPicker"
            class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm"
            style="background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.2);color:#A855F7;"
            title="Load a prompt from the library"
          >Load from Library</button>
        </div>
      </div>

      <!-- Library picker dropdown -->
      <div v-if="showLibraryPicker" class="mb-3 rounded-sm overflow-hidden" style="border:1px solid rgba(168,85,247,0.2);">
        <div class="px-3 py-2" style="background:rgba(168,85,247,0.05);border-bottom:1px solid rgba(168,85,247,0.15);">
          <span class="text-[10px] font-display tracking-wider uppercase text-purple-400">Select from Library</span>
        </div>
        <div v-if="libraryStore.loading" class="px-3 py-2 text-[10px] text-zinc-600">Loading...</div>
        <div v-else-if="libraryStore.versions.length === 0" class="px-3 py-2 text-[10px] text-zinc-600">
          No saved prompts yet.
          <router-link :to="{ name: 'PromptLibrary' }" class="text-purple-400 hover:text-purple-300 ml-1">Open Library</router-link>
        </div>
        <div v-else style="max-height:200px;overflow-y:auto;">
          <div
            v-for="v in libraryStore.versions"
            :key="v.id"
            class="px-3 py-2 cursor-pointer hover:bg-white/[0.03] transition-colors"
            style="border-top:1px solid var(--border-subtle);"
            @click="loadFromLibrary(v)"
          >
            <div class="flex items-center gap-2 mb-0.5">
              <span class="text-[10px] font-mono text-zinc-400">{{ v.label || `#${v.version_number || v.id?.slice(0,6)}` }}</span>
              <span class="text-[9px] px-1 rounded-sm"
                :style="v.source === 'tuner' || v.source === 'prompt_tuner'
                  ? 'background:rgba(168,85,247,0.1);color:#A855F7;'
                  : 'background:rgba(255,255,255,0.04);color:#71717A;'"
              >{{ v.source || 'manual' }}</span>
            </div>
            <div class="text-[10px] text-zinc-600 truncate">{{ v.prompt_text?.substring(0, 80) }}</div>
          </div>
        </div>
        <div class="px-3 py-2" style="border-top:1px solid var(--border-subtle);">
          <button
            @click="showLibraryPicker = false"
            class="text-[10px] text-zinc-600 hover:text-zinc-400 font-display tracking-wider uppercase"
          >Cancel</button>
        </div>
      </div>

      <textarea
        v-model="basePrompt"
        rows="4"
        class="w-full text-xs font-body px-3 py-2 rounded-sm resize-y"
        style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);color:#E4E4E7;outline:none;"
        placeholder="You are a helpful assistant that uses tools to answer questions..."
      ></textarea>
    </div>

    <!-- Tuning Config -->
    <div class="card rounded-md p-5 mb-6">
      <span class="section-label mb-3 block">Configuration</span>
      <div class="grid grid-cols-3 gap-4">
        <div>
          <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Population Size</label>
          <input
            v-model.number="populationSize"
            type="number" min="2" max="20"
            class="w-full px-2 py-1.5 rounded-sm text-xs font-mono text-zinc-200 mt-1"
            style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
          >
        </div>
        <div v-if="mode === 'evolutionary'">
          <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Generations</label>
          <input
            v-model.number="generations"
            type="number" min="1" max="10"
            class="w-full px-2 py-1.5 rounded-sm text-xs font-mono text-zinc-200 mt-1"
            style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
          >
        </div>
        <div v-if="mode === 'evolutionary'">
          <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Selection Ratio</label>
          <input
            v-model.number="selectionRatio"
            type="number" min="0.1" max="0.9" step="0.1"
            class="w-full px-2 py-1.5 rounded-sm text-xs font-mono text-zinc-200 mt-1"
            style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
          >
        </div>
      </div>

      <!-- Estimate -->
      <div v-if="estimate" class="mt-3 text-[10px] text-zinc-600 font-body">
        Estimated: {{ estimate.total_api_calls }} API calls, ~{{ formatDuration(estimate.estimated_duration_s) }}
        <span v-if="estimate.warning" class="text-yellow-400 ml-2">{{ estimate.warning }}</span>
      </div>
    </div>

    <!-- Start Button -->
    <div class="flex items-center justify-end">
      <button
        @click="startTuning"
        :disabled="!canStart"
        class="run-btn px-6 py-2 rounded-sm text-xs flex items-center gap-2"
        :class="{ 'opacity-50 cursor-not-allowed': !canStart }"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
        Start Prompt Tuning
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useToolEvalStore } from '../../stores/toolEval.js'
import { usePromptTunerStore } from '../../stores/promptTuner.js'
import { usePromptLibraryStore } from '../../stores/promptLibrary.js'
import { useConfigStore } from '../../stores/config.js'
import { useToast } from '../../composables/useToast.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import { getColor } from '../../utils/constants.js'

const router = useRouter()
const teStore = useToolEvalStore()
const prtStore = usePromptTunerStore()
const libraryStore = usePromptLibraryStore()
const configStore = useConfigStore()
const { showToast } = useToast()
const { context, setSuite } = useSharedContext()

// Library picker state
const showLibraryPicker = ref(false)

// --- State ---
const selectedSuiteId = ref('')
const mode = ref('quick')
const metaModelKey = ref('')
const selectedTargets = reactive(new Set())
const basePrompt = ref('You are a helpful assistant that uses tools to answer questions. When the user asks you to perform an action, use the appropriate tool.')
const populationSize = ref(5)
const generations = ref(3)
const selectionRatio = ref(0.4)
const loadingConfig = ref(true)
const providerGroups = ref([])
const estimate = ref(null)

const allModels = computed(() => configStore.allModels || [])

const canStart = computed(() => {
  return selectedSuiteId.value && metaModelKey.value && selectedTargets.size > 0 && !prtStore.isRunning
})

// --- Load ---
onMounted(async () => {
  if (teStore.suites.length === 0) {
    try { await teStore.loadSuites() } catch { showToast('Failed to load suites', 'error') }
  }

  try {
    await configStore.loadConfig()
    if (configStore.config) buildProviderGroups(configStore.config)
  } catch {
    showToast('Failed to load model config', 'error')
  } finally {
    loadingConfig.value = false
  }

  // Restore context
  if (context.suiteId) selectedSuiteId.value = context.suiteId
  if (context.selectedModels?.length) {
    context.selectedModels.forEach(m => selectedTargets.add(m))
  }
  if (context.systemPrompts?._global) {
    basePrompt.value = context.systemPrompts._global
  }
})

// Fetch estimate when config changes
watch([selectedSuiteId, mode, populationSize, generations, () => selectedTargets.size], async () => {
  if (!selectedSuiteId.value) { estimate.value = null; return }
  try {
    estimate.value = await prtStore.getEstimate({
      suite_id: selectedSuiteId.value,
      mode: mode.value,
      population_size: populationSize.value,
      generations: mode.value === 'quick' ? 1 : generations.value,
      num_models: selectedTargets.size || 1,
    })
  } catch { estimate.value = null }
}, { debounce: 500 })

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

function toggleTarget(key) {
  if (selectedTargets.has(key)) selectedTargets.delete(key)
  else selectedTargets.add(key)
}

function onSuiteChange() {
  const suite = teStore.suites.find(s => s.id === selectedSuiteId.value)
  if (suite) setSuite(suite.id, suite.name)
}

function formatDuration(s) {
  if (!s) return '?'
  if (s < 60) return `${Math.round(s)}s`
  return `${Math.ceil(s / 60)}m`
}

// --- Prompt Library ---

async function openLibraryPicker() {
  showLibraryPicker.value = !showLibraryPicker.value
  if (showLibraryPicker.value && libraryStore.versions.length === 0) {
    try { await libraryStore.loadVersions() } catch { showToast('Failed to load prompt library', 'error') }
  }
}

function loadFromLibrary(v) {
  basePrompt.value = v.prompt_text
  showLibraryPicker.value = false
  showToast(`Loaded: ${v.label || 'prompt version'}`, 'success')
}

async function saveCurrentToLibrary() {
  if (!basePrompt.value.trim()) return
  try {
    await libraryStore.saveVersion(basePrompt.value.trim(), null, 'manual')
    showToast('Prompt saved to library', 'success')
  } catch {
    showToast('Failed to save to library', 'error')
  }
}

// --- Start ---
async function startTuning() {
  if (!canStart.value) return

  const metaIdx = metaModelKey.value.indexOf('::')
  const metaProviderKey = metaModelKey.value.substring(0, metaIdx)
  const metaModelId = metaModelKey.value.substring(metaIdx + 2)

  const targetTargets = Array.from(selectedTargets).map(k => {
    const i = k.indexOf('::')
    return { provider_key: k.substring(0, i), model_id: k.substring(i + 2) }
  })
  const targetModels = Array.from(selectedTargets).map(k => k.substring(k.indexOf('::') + 2))

  const body = {
    suite_id: selectedSuiteId.value,
    mode: mode.value,
    meta_model: metaModelId,
    meta_provider_key: metaProviderKey,
    target_models: targetModels,
    target_targets: targetTargets,
    base_prompt: basePrompt.value,
    config: {
      population_size: populationSize.value,
      generations: mode.value === 'quick' ? 1 : generations.value,
      selection_ratio: selectionRatio.value,
    },
  }

  if (context.experimentId) {
    body.experiment_id = context.experimentId
  }

  try {
    await prtStore.startTuning(body)
    showToast('Prompt tuning started', 'success')
    router.push({ name: 'PromptTunerRun' })
  } catch (e) {
    showToast(e.message || 'Failed to start tuning', 'error')
  }
}
</script>
