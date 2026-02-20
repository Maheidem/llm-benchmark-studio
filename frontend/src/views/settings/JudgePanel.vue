<template>
  <div>
    <div v-if="loading" class="text-zinc-600 text-sm font-body">Loading settings...</div>
    <div v-else class="space-y-6">
      <!-- Judge Model Configuration -->
      <div class="card rounded-md overflow-hidden">
        <div class="px-5 py-3" style="border-bottom:1px solid var(--border-subtle)">
          <span class="section-label">Judge Model</span>
        </div>
        <div class="px-5 py-4 space-y-4">
          <!-- Enabled toggle -->
          <div class="flex items-center gap-3">
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" v-model="judge.enabled" @change="debounceSave" class="accent-lime-400">
              <span class="text-xs font-body text-zinc-300">Auto-judge enabled</span>
            </label>
          </div>

          <!-- Model selector -->
          <div>
            <label class="field-label">Judge Model</label>
            <select v-model="judge.selectedModel" @change="debounceSave" class="settings-select">
              <option value="">-- Select a model --</option>
              <option v-for="m in allModels" :key="m.value" :value="m.value">{{ m.label }}</option>
            </select>
          </div>

          <!-- Mode -->
          <div>
            <label class="field-label">Judge Mode</label>
            <select v-model="judge.mode" @change="debounceSave" class="settings-select">
              <option value="post_eval">Post-evaluation (after each eval run)</option>
              <option value="manual">Manual only</option>
            </select>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <!-- Temperature -->
            <div>
              <label class="field-label">Temperature</label>
              <input v-model.number="judge.temperature" type="number" step="0.1" min="0" max="2" @change="debounceSave" class="settings-input">
            </div>

            <!-- Max Tokens -->
            <div>
              <label class="field-label">Max Tokens</label>
              <input v-model.number="judge.max_tokens" type="number" step="256" min="256" @change="debounceSave" class="settings-input">
            </div>

            <!-- Concurrency -->
            <div>
              <label class="field-label">Concurrency</label>
              <input v-model.number="judge.concurrency" type="number" min="1" max="20" @change="debounceSave" class="settings-input">
            </div>
          </div>

          <!-- Custom Instructions -->
          <div>
            <label class="field-label">Custom Instructions</label>
            <p class="text-[10px] text-zinc-700 font-body mb-2">Additional instructions appended to the judge system prompt.</p>
            <textarea
              v-model="judge.custom_instructions"
              @input="debounceSave"
              rows="4"
              class="w-full px-3 py-2 rounded-sm text-xs font-mono text-zinc-200"
              style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;resize:vertical;"
              placeholder="e.g. Focus on correctness over style. Penalize hallucinations heavily."
            ></textarea>
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
import { ref, reactive, onMounted } from 'vue'
import { apiFetch } from '../../utils/api.js'
import { useToast } from '../../composables/useToast.js'

const { showToast } = useToast()

const loading = ref(true)
const saveMsg = ref('')
const saveOk = ref(true)
let saveTimer = null

const judge = reactive({
  enabled: false,
  selectedModel: '',
  mode: 'post_eval',
  temperature: 0.0,
  max_tokens: 4096,
  concurrency: 4,
  custom_instructions: '',
})

const allModels = ref([])

async function loadSettings() {
  loading.value = true
  try {
    // Load config for model list
    const configRes = await apiFetch('/api/config')
    const configData = await configRes.json()

    // Build model list
    const models = []
    for (const [provName, provData] of Object.entries(configData.providers || {})) {
      const pk = provData.provider_key || provName
      const provModels = Array.isArray(provData) ? provData : (provData.models || [])
      for (const m of provModels) {
        const ck = pk + '::' + m.model_id
        models.push({ value: ck, label: `${m.display_name || m.model_id} (${provName})` })
      }
    }
    allModels.value = models

    // Load phase10 settings
    const settingsRes = await apiFetch('/api/settings/phase10')
    if (settingsRes.ok) {
      const settings = await settingsRes.json()
      const j = settings.judge || {}
      judge.enabled = !!j.enabled
      judge.mode = j.mode || 'post_eval'
      judge.temperature = j.temperature ?? 0.0
      judge.max_tokens = j.max_tokens || 4096
      judge.concurrency = j.concurrency || 4
      judge.custom_instructions = j.custom_instructions || ''
      // Reconstruct selected model compound key
      if (j.provider_key && j.model_id) {
        judge.selectedModel = j.provider_key + '::' + j.model_id
      } else if (j.model_id) {
        judge.selectedModel = j.model_id
      }
    }
  } catch (e) {
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
  // Parse compound key
  let provider_key = ''
  let model_id = ''
  if (judge.selectedModel.includes('::')) {
    const i = judge.selectedModel.indexOf('::')
    provider_key = judge.selectedModel.substring(0, i)
    model_id = judge.selectedModel.substring(i + 2)
  } else {
    model_id = judge.selectedModel
  }

  const data = {
    judge: {
      enabled: judge.enabled,
      model_id,
      provider_key,
      mode: judge.mode,
      temperature: parseFloat(judge.temperature) || 0.0,
      max_tokens: parseInt(judge.max_tokens) || 4096,
      custom_instructions: judge.custom_instructions,
      concurrency: parseInt(judge.concurrency) || 4,
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
</style>
