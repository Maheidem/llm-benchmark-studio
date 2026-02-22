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
          <!-- Default Judge Model selector -->
          <div>
            <label class="field-label">Default Judge Model</label>
            <select v-model="judge.default_judge_model" @change="debounceSave" class="settings-select">
              <option value="">-- Select a model --</option>
              <option v-for="m in allModels" :key="m.value" :value="m.value">{{ m.label }}</option>
            </select>
          </div>

          <!-- Default Judge Provider Key -->
          <div>
            <label class="field-label">Default Judge Provider Key</label>
            <input
              v-model="judge.default_judge_provider_key"
              type="text"
              @change="debounceSave"
              class="settings-input"
              placeholder="e.g. openai, anthropic, lm_studio"
            >
          </div>

          <!-- Mode -->
          <div>
            <label class="field-label">Default Mode</label>
            <select v-model="judge.default_mode" @change="debounceSave" class="settings-select">
              <option value="post_eval">Post-evaluation (after each eval run)</option>
              <option value="live_inline">Live inline</option>
            </select>
          </div>

          <!-- Score Override Policy -->
          <div>
            <label class="field-label">Score Override Policy</label>
            <select v-model="judge.score_override_policy" @change="debounceSave" class="settings-select">
              <option value="always_allow">Always Allow</option>
              <option value="require_confirmation">Require Confirmation</option>
              <option value="never">Never</option>
            </select>
            <p class="text-[10px] text-zinc-700 font-body mt-1">Controls whether the judge can override automated scores when it detects functional equivalence.</p>
          </div>

          <!-- Auto Judge After Eval + Concurrency row -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <!-- Auto Judge After Eval -->
            <div class="flex items-center gap-3 pt-4">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="judge.auto_judge_after_eval" @change="debounceSave" class="accent-lime-400">
                <span class="text-xs font-body text-zinc-300">Auto-judge after eval</span>
              </label>
            </div>

            <!-- Concurrency -->
            <div>
              <label class="field-label">Concurrency</label>
              <input v-model.number="judge.concurrency" type="number" min="1" max="20" @change="debounceSave" class="settings-input">
            </div>
          </div>

          <!-- Custom Instructions Template -->
          <div>
            <label class="field-label">Custom Instructions Template</label>
            <p class="text-[10px] text-zinc-700 font-body mb-2">Additional instructions appended to the judge system prompt. Used as default for new judge runs.</p>
            <textarea
              v-model="judge.custom_instructions_template"
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
  default_judge_model: '',
  default_judge_provider_key: '',
  default_mode: 'post_eval',
  custom_instructions_template: '',
  score_override_policy: 'always_allow',
  auto_judge_after_eval: false,
  concurrency: 4,
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

    // Load judge settings from correct endpoint
    const settingsRes = await apiFetch('/api/settings/judge')
    if (settingsRes.ok) {
      const s = await settingsRes.json()
      judge.default_judge_model = s.default_judge_model || ''
      judge.default_judge_provider_key = s.default_judge_provider_key || ''
      judge.default_mode = s.default_mode || 'post_eval'
      judge.custom_instructions_template = s.custom_instructions_template || ''
      judge.score_override_policy = s.score_override_policy || 'always_allow'
      judge.auto_judge_after_eval = !!s.auto_judge_after_eval
      judge.concurrency = s.concurrency || 4
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
  const data = {
    default_judge_model: judge.default_judge_model || null,
    default_judge_provider_key: judge.default_judge_provider_key || null,
    default_mode: judge.default_mode,
    custom_instructions_template: judge.custom_instructions_template,
    score_override_policy: judge.score_override_policy,
    auto_judge_after_eval: judge.auto_judge_after_eval,
    concurrency: parseInt(judge.concurrency) || 4,
  }

  try {
    const res = await apiFetch('/api/settings/judge', {
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
