<template>
  <div>
    <div v-if="loading" class="text-zinc-600 text-sm font-body">Loading settings...</div>
    <div v-else class="space-y-6">
      <!-- Parameter Tuner Defaults -->
      <div class="card rounded-md overflow-hidden">
        <div class="px-5 py-3 flex items-center justify-between cursor-pointer" style="border-bottom:1px solid var(--border-subtle)" @click="sections.paramTuner = !sections.paramTuner">
          <span class="section-label">Parameter Tuner Defaults</span>
          <svg class="w-4 h-4 text-zinc-600 transition-transform" :class="{ 'rotate-180': sections.paramTuner }" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>
        </div>
        <div v-if="sections.paramTuner" class="px-5 py-4">
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label class="field-label">Max Combinations</label>
              <input v-model.number="paramTuner.max_combinations" type="number" @change="debounceSave" class="settings-input">
            </div>
          </div>
          <div class="mt-4">
            <span class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-2">Temperature Range</span>
            <div class="grid grid-cols-3 gap-4">
              <div>
                <label class="field-label">Min</label>
                <input v-model.number="paramTuner.temp_min" type="number" step="0.1" @change="debounceSave" class="settings-input">
              </div>
              <div>
                <label class="field-label">Max</label>
                <input v-model.number="paramTuner.temp_max" type="number" step="0.1" @change="debounceSave" class="settings-input">
              </div>
              <div>
                <label class="field-label">Step</label>
                <input v-model.number="paramTuner.temp_step" type="number" step="0.1" @change="debounceSave" class="settings-input">
              </div>
            </div>
          </div>
          <div class="mt-4">
            <span class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-2">Top P Range</span>
            <div class="grid grid-cols-3 gap-4">
              <div>
                <label class="field-label">Min</label>
                <input v-model.number="paramTuner.top_p_min" type="number" step="0.05" @change="debounceSave" class="settings-input">
              </div>
              <div>
                <label class="field-label">Max</label>
                <input v-model.number="paramTuner.top_p_max" type="number" step="0.05" @change="debounceSave" class="settings-input">
              </div>
              <div>
                <label class="field-label">Step</label>
                <input v-model.number="paramTuner.top_p_step" type="number" step="0.05" @change="debounceSave" class="settings-input">
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Prompt Tuner Defaults -->
      <div class="card rounded-md overflow-hidden">
        <div class="px-5 py-3 flex items-center justify-between cursor-pointer" style="border-bottom:1px solid var(--border-subtle)" @click="sections.promptTuner = !sections.promptTuner">
          <span class="section-label">Prompt Tuner Defaults</span>
          <svg class="w-4 h-4 text-zinc-600 transition-transform" :class="{ 'rotate-180': sections.promptTuner }" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>
        </div>
        <div v-if="sections.promptTuner" class="px-5 py-4">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label class="field-label">Mode</label>
              <select v-model="promptTuner.mode" @change="debounceSave" class="settings-select">
                <option value="quick">Quick</option>
                <option value="thorough">Thorough</option>
                <option value="exhaustive">Exhaustive</option>
              </select>
            </div>
            <div>
              <label class="field-label">Max API Calls</label>
              <input v-model.number="promptTuner.max_api_calls" type="number" @change="debounceSave" class="settings-input">
            </div>
            <div>
              <label class="field-label">Generations</label>
              <input v-model.number="promptTuner.generations" type="number" @change="debounceSave" class="settings-input">
            </div>
            <div>
              <label class="field-label">Population Size</label>
              <input v-model.number="promptTuner.population_size" type="number" @change="debounceSave" class="settings-input">
            </div>
          </div>
        </div>
      </div>

      <!-- Param Support Configuration -->
      <div class="card rounded-md overflow-hidden">
        <div class="px-5 py-3 flex items-center justify-between cursor-pointer" style="border-bottom:1px solid var(--border-subtle)" @click="sections.paramSupport = !sections.paramSupport">
          <span class="section-label">Provider Parameter Support</span>
          <svg class="w-4 h-4 text-zinc-600 transition-transform" :class="{ 'rotate-180': sections.paramSupport }" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>
        </div>
        <div v-if="sections.paramSupport" class="px-5 py-4">
          <div v-if="!paramSupport" class="text-center py-4">
            <p class="text-zinc-600 text-xs font-body mb-3">Parameter support not configured yet.</p>
            <button @click="seedParamSupport" class="lime-btn">Initialize Parameter Support</button>
          </div>
          <div v-else>
            <div class="flex items-center gap-3 mb-4">
              <select v-model="psCurrentProvider" @change="renderParamTable" class="settings-select" style="max-width:240px;">
                <option v-for="pk in psProviders" :key="pk" :value="pk">{{ paramSupport.provider_defaults[pk]?.display_name || pk }}</option>
              </select>
              <button @click="seedParamSupport" class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300 transition-colors px-2 py-1" style="border:1px solid var(--border-subtle);border-radius:2px;">Reset to Defaults</button>
            </div>

            <!-- Param table -->
            <div v-if="currentParams && Object.keys(currentParams).length > 0" class="overflow-x-auto">
              <table class="w-full text-xs">
                <thead>
                  <tr style="border-bottom:1px solid var(--border-subtle)">
                    <th class="px-2 py-1.5 text-left text-[10px] font-display tracking-wider uppercase text-zinc-500">Param</th>
                    <th class="px-2 py-1.5 text-center text-[10px] font-display tracking-wider uppercase text-zinc-500">Enabled</th>
                    <th class="px-2 py-1.5 text-center text-[10px] font-display tracking-wider uppercase text-zinc-500">Type</th>
                    <th class="px-2 py-1.5 text-right text-[10px] font-display tracking-wider uppercase text-zinc-500">Min</th>
                    <th class="px-2 py-1.5 text-right text-[10px] font-display tracking-wider uppercase text-zinc-500">Max</th>
                    <th class="px-2 py-1.5 text-right text-[10px] font-display tracking-wider uppercase text-zinc-500">Step</th>
                    <th class="px-2 py-1.5 text-right text-[10px] font-display tracking-wider uppercase text-zinc-500">Default</th>
                    <th class="px-2 py-1.5 text-center text-[10px] font-display tracking-wider uppercase text-zinc-500"></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(spec, pname) in currentParams" :key="pname" style="border-bottom:1px solid rgba(255,255,255,0.03)">
                    <td class="px-2 py-2 font-mono text-zinc-300">{{ pname }}</td>
                    <td class="px-2 py-2 text-center">
                      <input type="checkbox" :checked="spec.enabled !== false" @change="onParamChange(pname, 'enabled', $event.target.checked)" class="accent-lime-400">
                    </td>
                    <td class="px-2 py-2 text-center text-zinc-500">{{ spec.type || '-' }}</td>
                    <td class="px-2 py-2 text-right">
                      <input v-if="isNumType(spec.type)" type="number" :value="spec.min" @change="onParamChange(pname, 'min', parseFloat($event.target.value))" class="param-input">
                      <span v-else>-</span>
                    </td>
                    <td class="px-2 py-2 text-right">
                      <input v-if="isNumType(spec.type)" type="number" :value="spec.max" @change="onParamChange(pname, 'max', parseFloat($event.target.value))" class="param-input">
                      <span v-else>-</span>
                    </td>
                    <td class="px-2 py-2 text-right">
                      <input v-if="isNumType(spec.type)" type="number" :value="spec.step" @change="onParamChange(pname, 'step', parseFloat($event.target.value))" class="param-input">
                      <span v-else>-</span>
                    </td>
                    <td class="px-2 py-2 text-right">
                      <input v-if="spec.default !== undefined" :type="isNumType(spec.type) ? 'number' : 'text'" :value="spec.default" @change="onParamChange(pname, 'default', isNumType(spec.type) ? parseFloat($event.target.value) : $event.target.value)" class="param-input">
                      <span v-else>-</span>
                    </td>
                    <td class="px-2 py-2 text-center">
                      <button @click="removeParam(pname)" class="text-zinc-700 hover:text-red-400 transition-colors" title="Remove">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="text-xs text-zinc-600 font-body text-center py-3">No parameters configured for this provider.</div>

            <button @click="addParam" class="mt-3 text-[10px] font-mono text-zinc-500 hover:text-zinc-300 px-2 py-1 rounded-sm" style="border:1px solid var(--border-subtle)">+ Add Parameter</button>
          </div>
        </div>
      </div>

      <!-- Save status -->
      <div v-if="saveMsg" class="text-[11px] font-body" :style="`color:${saveOk ? 'var(--lime)' : 'var(--coral)'}`">
        {{ saveMsg }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { apiFetch } from '../../utils/api.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'

const { showToast } = useToast()
const { inputModal } = useModal()

const loading = ref(true)
const saveMsg = ref('')
const saveOk = ref(true)
let saveTimer = null
let psSaveTimer = null

const sections = reactive({
  paramTuner: true,
  promptTuner: false,
  paramSupport: false,
})

const paramTuner = reactive({
  max_combinations: 50,
  temp_min: 0,
  temp_max: 1,
  temp_step: 0.5,
  top_p_min: 0.5,
  top_p_max: 1,
  top_p_step: 0.25,
})

const promptTuner = reactive({
  mode: 'quick',
  max_api_calls: 100,
  generations: 3,
  population_size: 5,
})

const paramSupport = ref(null)
const psCurrentProvider = ref('')

const psProviders = computed(() => {
  if (!paramSupport.value?.provider_defaults) return []
  return Object.keys(paramSupport.value.provider_defaults).sort()
})

const currentParams = computed(() => {
  if (!paramSupport.value?.provider_defaults?.[psCurrentProvider.value]) return {}
  return paramSupport.value.provider_defaults[psCurrentProvider.value].params || {}
})

function isNumType(type) {
  return type === 'float' || type === 'int'
}

function renderParamTable() {
  // Reactivity handles rendering via computed
}

async function loadSettings() {
  loading.value = true
  try {
    const res = await apiFetch('/api/settings/phase10')
    if (res.ok) {
      const settings = await res.json()

      // Param tuner
      const pt = settings.param_tuner || {}
      paramTuner.max_combinations = pt.max_combinations ?? 50
      paramTuner.temp_min = pt.temp_min ?? 0
      paramTuner.temp_max = pt.temp_max ?? 1
      paramTuner.temp_step = pt.temp_step ?? 0.5
      paramTuner.top_p_min = pt.top_p_min ?? 0.5
      paramTuner.top_p_max = pt.top_p_max ?? 1
      paramTuner.top_p_step = pt.top_p_step ?? 0.25

      // Prompt tuner
      const prt = settings.prompt_tuner || {}
      promptTuner.mode = prt.mode || 'quick'
      promptTuner.max_api_calls = prt.max_api_calls ?? 100
      promptTuner.generations = prt.generations ?? 3
      promptTuner.population_size = prt.population_size ?? 5

      // Param support
      if (settings.param_support && settings.param_support.provider_defaults && Object.keys(settings.param_support.provider_defaults).length > 0) {
        paramSupport.value = settings.param_support
        if (psProviders.value.length > 0) {
          psCurrentProvider.value = psProviders.value[0]
        }
      }
    }
  } catch {
    showToast('Failed to load settings', 'error')
  } finally {
    loading.value = false
  }
}

function debounceSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(save, 500)
}

async function save() {
  const data = {
    param_tuner: {
      max_combinations: parseInt(paramTuner.max_combinations) || 50,
      temp_min: parseFloat(paramTuner.temp_min) || 0,
      temp_max: parseFloat(paramTuner.temp_max) || 1,
      temp_step: parseFloat(paramTuner.temp_step) || 0.5,
      top_p_min: parseFloat(paramTuner.top_p_min) || 0.5,
      top_p_max: parseFloat(paramTuner.top_p_max) || 1,
      top_p_step: parseFloat(paramTuner.top_p_step) || 0.25,
    },
    prompt_tuner: {
      mode: promptTuner.mode || 'quick',
      max_api_calls: parseInt(promptTuner.max_api_calls) || 100,
      generations: parseInt(promptTuner.generations) || 3,
      population_size: parseInt(promptTuner.population_size) || 5,
    },
  }

  try {
    const res = await apiFetch('/api/settings/phase10', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (res.ok) {
      saveMsg.value = 'Saved'
      saveOk.value = true
      setTimeout(() => { saveMsg.value = '' }, 3000)
    } else {
      saveMsg.value = 'Failed to save'
      saveOk.value = false
    }
  } catch {
    saveMsg.value = 'Network error'
    saveOk.value = false
  }
}

async function seedParamSupport() {
  try {
    const res = await apiFetch('/api/param-support/seed', { method: 'POST' })
    if (!res.ok) {
      showToast('Failed to seed param support', 'error')
      return
    }
    const data = await res.json()
    paramSupport.value = data
    // Save to phase10 settings
    await apiFetch('/api/settings/phase10', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ param_support: data }),
    })
    if (psProviders.value.length > 0) {
      psCurrentProvider.value = psProviders.value[0]
    }
    showToast('Parameter support initialized', 'success')
  } catch {
    showToast('Failed to seed param support', 'error')
  }
}

function onParamChange(paramName, field, value) {
  if (!paramSupport.value?.provider_defaults?.[psCurrentProvider.value]?.params?.[paramName]) return
  paramSupport.value.provider_defaults[psCurrentProvider.value].params[paramName][field] = value
  debouncePsSave()
}

function removeParam(paramName) {
  if (!paramSupport.value?.provider_defaults?.[psCurrentProvider.value]?.params) return
  delete paramSupport.value.provider_defaults[psCurrentProvider.value].params[paramName]
  // Force reactivity
  paramSupport.value = { ...paramSupport.value }
  debouncePsSave()
}

async function addParam() {
  const result = await inputModal('Add Parameter', 'Parameter name (e.g. top_k)')
  const name = typeof result === 'object' ? result?.value : result
  if (!name || !name.trim()) return
  const trimmed = name.trim()
  if (!paramSupport.value?.provider_defaults?.[psCurrentProvider.value]) return
  const params = paramSupport.value.provider_defaults[psCurrentProvider.value].params
  if (!params) {
    paramSupport.value.provider_defaults[psCurrentProvider.value].params = {}
  }
  if (paramSupport.value.provider_defaults[psCurrentProvider.value].params[trimmed]) {
    showToast('Parameter already exists', 'error')
    return
  }
  paramSupport.value.provider_defaults[psCurrentProvider.value].params[trimmed] = {
    enabled: true, type: 'float', min: 0, max: 1, step: 0.1, default: 0,
  }
  paramSupport.value = { ...paramSupport.value }
  debouncePsSave()
  showToast(`Parameter "${trimmed}" added`, 'success')
}

function debouncePsSave() {
  if (psSaveTimer) clearTimeout(psSaveTimer)
  psSaveTimer = setTimeout(async () => {
    try {
      await apiFetch('/api/settings/phase10', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ param_support: paramSupport.value }),
      })
    } catch {
      showToast('Failed to save param support config', 'error')
    }
  }, 600)
}

onMounted(loadSettings)
</script>

<style scoped>
.settings-input {
  width: 100%;
  padding: 8px 12px;
  border-radius: 2px;
  font-size: 14px;
  font-family: 'Space Mono', monospace;
  color: #E4E4E7;
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border-subtle);
  outline: none;
  transition: border-color 0.2s;
}
.settings-input:focus {
  border-color: rgba(191,255,0,0.3);
}
.settings-select {
  width: 100%;
  padding: 8px 12px;
  border-radius: 2px;
  font-size: 13px;
  font-family: 'Outfit', sans-serif;
  color: #E4E4E7;
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border-subtle);
  outline: none;
  transition: border-color 0.2s;
  appearance: auto;
}
.settings-select:focus {
  border-color: rgba(191,255,0,0.3);
}
.field-label {
  font-size: 10px;
  color: #71717A;
  font-family: 'Chakra Petch', sans-serif;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  display: block;
  margin-bottom: 4px;
}
.lime-btn {
  font-size: 11px;
  font-family: 'Chakra Petch', sans-serif;
  font-weight: 500;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  padding: 8px 16px;
  border-radius: 2px;
  color: var(--lime);
  border: 1px solid rgba(191,255,0,0.2);
  background: transparent;
  cursor: pointer;
  transition: border-color 0.15s;
}
.lime-btn:hover {
  border-color: rgba(191,255,0,0.5);
}
.param-input {
  padding: 4px 8px;
  border-radius: 2px;
  font-size: 12px;
  font-family: 'Space Mono', monospace;
  color: #E4E4E7;
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border-subtle);
  outline: none;
  width: 70px;
}
.param-input:focus {
  border-color: rgba(191,255,0,0.3);
}
</style>
