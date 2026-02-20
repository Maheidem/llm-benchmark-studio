<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Param Tuner</h2>
        <p class="text-sm text-zinc-600 font-body">Grid search across parameter combinations to find optimal tool calling config.</p>
      </div>
      <div class="flex items-center gap-2">
        <router-link v-if="ptStore.isRunning" :to="{ name: 'ParamTunerRun' }"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
          style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
        >View Active Run</router-link>
        <router-link :to="{ name: 'ParamTunerHistory' }"
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
          <div class="provider-group-header" @click="group.collapsed = !group.collapsed">
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

    <!-- Preset Manager -->
    <PresetManager
      v-if="selectedModels.size > 0"
      :presets="presets"
      :current-search-space="currentSearchSpace"
      @load="onPresetLoad"
      @update:presets="presets = $event"
    />

    <!-- Search Space Builder -->
    <SearchSpaceBuilder
      v-if="selectedModels.size > 0"
      :param-defs="paramDefs"
      v-model="currentSearchSpace"
      @update:totalCombos="totalCombos = $event"
    />

    <!-- Compatibility Matrix -->
    <CompatibilityMatrix
      v-if="selectedModels.size > 0 && Object.keys(currentSearchSpace).length > 0"
      :models="matrixModels"
      :enabled-params="Object.keys(currentSearchSpace)"
      :param-support="paramSupport"
      :registry="paramsRegistry"
    />

    <!-- Start Button -->
    <div class="flex items-center justify-between mt-6">
      <div class="text-xs text-zinc-600 font-body">
        <span v-if="totalCombos > 0">{{ totalCombos }} combos x {{ selectedModels.size }} model{{ selectedModels.size !== 1 ? 's' : '' }} = {{ totalCombos * selectedModels.size }} total evaluations</span>
      </div>
      <button
        @click="startTuning"
        :disabled="!canStart"
        class="run-btn px-6 py-2 rounded-sm text-xs flex items-center gap-2"
        :class="{ 'opacity-50 cursor-not-allowed': !canStart }"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
        Start Tuning
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useToolEvalStore } from '../../stores/toolEval.js'
import { useParamTunerStore } from '../../stores/paramTuner.js'
import { useConfigStore } from '../../stores/config.js'
import { useToast } from '../../composables/useToast.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import { apiFetch } from '../../utils/api.js'
import { getColor } from '../../utils/constants.js'
import SearchSpaceBuilder from '../../components/tool-eval/SearchSpaceBuilder.vue'
import CompatibilityMatrix from '../../components/tool-eval/CompatibilityMatrix.vue'
import PresetManager from '../../components/tool-eval/PresetManager.vue'

const router = useRouter()
const teStore = useToolEvalStore()
const ptStore = useParamTunerStore()
const configStore = useConfigStore()
const { showToast } = useToast()
const { context, setSuite, setModels } = useSharedContext()

// --- State ---
const selectedSuiteId = ref('')
const selectedModels = reactive(new Set())
const loadingConfig = ref(true)
const providerGroups = ref([])
const currentSearchSpace = ref({})
const totalCombos = ref(0)
const presets = ref([])
const paramDefs = ref([])
const paramSupport = ref(null)
const paramsRegistry = ref(null)

const canStart = computed(() => {
  return selectedSuiteId.value && selectedModels.size > 0 && totalCombos.value > 0 && !ptStore.isRunning
})

const matrixModels = computed(() => {
  const models = []
  for (const group of providerGroups.value) {
    for (const m of group.models) {
      if (selectedModels.has(m.key)) {
        models.push({
          id: m.model_id,
          shortName: m.display_name || m.model_id.split('/').pop(),
          rk: m.registryKey || m.provider_key,
          providerKey: m.provider_key,
        })
      }
    }
  }
  return models
})

// --- Load ---
onMounted(async () => {
  if (teStore.suites.length === 0) {
    try { await teStore.loadSuites() } catch { /* ignore */ }
  }

  try {
    if (!configStore.config) await configStore.loadConfig()
    if (configStore.config) buildProviderGroups(configStore.config)
  } catch {
    showToast('Failed to load model config', 'error')
  } finally {
    loadingConfig.value = false
  }

  // Load registry
  try {
    await configStore.loadParamsRegistry()
    paramsRegistry.value = configStore.providerParamsRegistry
  } catch { /* ignore */ }

  // Load Phase 10 settings for presets & param_support
  try {
    const res = await apiFetch('/api/settings/phase10')
    if (res.ok) {
      const data = await res.json()
      presets.value = data.param_tuner?.presets || []
      paramSupport.value = data.param_support || null
    }
  } catch { /* ignore */ }

  // Restore context
  if (context.suiteId) selectedSuiteId.value = context.suiteId
  if (context.selectedModels?.length) {
    context.selectedModels.forEach(m => selectedModels.add(m))
    buildParamDefs()
  }
})

// Rebuild param defs when model selection changes
watch(() => selectedModels.size, () => {
  buildParamDefs()
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
      registryKey: prov.provider_key || provKey,
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

function buildParamDefs() {
  // Build standard parameter definitions based on selected models
  const totalModels = selectedModels.size
  if (totalModels === 0) {
    paramDefs.value = []
    return
  }

  const standard = [
    { name: 'temperature', type: 'float', min: 0, max: 2, step: 0.1 },
    { name: 'top_p', type: 'float', min: 0, max: 1, step: 0.1 },
    { name: 'top_k', type: 'int', min: 1, max: 100, step: 10 },
    { name: 'tool_choice', type: 'enum', values: ['auto', 'required', 'none'] },
    { name: 'frequency_penalty', type: 'float', min: -2, max: 2, step: 0.1 },
    { name: 'presence_penalty', type: 'float', min: -2, max: 2, step: 0.1 },
    { name: 'repetition_penalty', type: 'float', min: 0.5, max: 2, step: 0.1 },
    { name: 'min_p', type: 'float', min: 0, max: 1, step: 0.05 },
  ]

  paramDefs.value = standard.map(p => ({
    ...p,
    supportedBy: totalModels,  // simplified; compat matrix shows real support
    totalModels,
    locked: false,
    lockedValue: null,
    notes: [],
  }))
}

// --- Model selection ---
function toggleModel(key) {
  if (selectedModels.has(key)) selectedModels.delete(key)
  else selectedModels.add(key)
  setModels(Array.from(selectedModels))
}

function allProviderSelected(group) {
  return group.models.every(m => selectedModels.has(m.key))
}

function toggleProvider(group) {
  const allSel = allProviderSelected(group)
  for (const m of group.models) {
    if (allSel) selectedModels.delete(m.key)
    else selectedModels.add(m.key)
  }
  setModels(Array.from(selectedModels))
}

function onSuiteChange() {
  const suite = teStore.suites.find(s => s.id === selectedSuiteId.value)
  if (suite) setSuite(suite.id, suite.name)
}

function onPresetLoad(searchSpace) {
  currentSearchSpace.value = { ...searchSpace }
}

// --- Start Tuning ---
async function startTuning() {
  if (!canStart.value) return

  const targets = Array.from(selectedModels).map(k => {
    const i = k.indexOf('::')
    return { provider_key: k.substring(0, i), model_id: k.substring(i + 2) }
  })

  const body = {
    suite_id: selectedSuiteId.value,
    models: Array.from(selectedModels).map(k => k.substring(k.indexOf('::') + 2)),
    targets,
    search_space: currentSearchSpace.value,
  }

  if (context.experimentId) {
    body.experiment_id = context.experimentId
  }

  try {
    await ptStore.startTuning(body)
    showToast('Param tuning started', 'success')
    router.push({ name: 'ParamTunerRun' })
  } catch (e) {
    showToast(e.message || 'Failed to start tuning', 'error')
  }
}
</script>
