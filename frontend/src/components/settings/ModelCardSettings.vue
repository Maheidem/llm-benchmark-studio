<template>
  <div class="px-5 py-4">
    <!-- Header -->
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-3">
        <div class="text-[13px] font-medium text-zinc-200 font-body">{{ form.display_name || model.display_name }}</div>
        <span class="text-[9px] font-mono text-zinc-700">{{ model.model_id }}</span>
      </div>
      <div class="flex gap-2">
        <button @click="expanded = !expanded" class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300 transition-colors">
          {{ expanded ? 'Collapse' : 'Edit' }}
        </button>
        <button @click="saveModel" class="lime-btn">Save</button>
        <button
          @click="$emit('delete', model)"
          class="text-[11px] font-display tracking-wider uppercase px-2 py-1.5 rounded-sm text-zinc-600 hover:text-red-400 transition-colors"
          style="border:1px solid var(--border-subtle)"
        >&times;</button>
      </div>
    </div>

    <!-- Expanded edit form -->
    <div v-if="expanded">
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label class="field-label">Model ID</label>
          <input
            v-model="form.model_id"
            class="settings-input"
            list="dl-model-edit"
            @focus="$parent?.$emit?.('loadDiscovery', providerKey)"
          >
          <datalist id="dl-model-edit">
            <option v-for="dm in discoveredModels" :key="dm.id" :value="dm.id">{{ dm.display_name }}</option>
          </datalist>
        </div>
        <div>
          <label class="field-label">Display Name</label>
          <input v-model="form.display_name" class="settings-input">
        </div>
        <div>
          <label class="field-label">Context Window</label>
          <input v-model.number="form.context_window" type="number" class="settings-input">
          <div class="flex gap-1.5 mt-2 flex-wrap">
            <button
              v-for="(size, i) in QUICK_CTX_SIZES"
              :key="size"
              @click="form.context_window = size"
              class="text-[9px] font-mono px-2 py-0.5 rounded-sm text-zinc-600 hover:text-zinc-400 transition-colors"
              style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle)"
            >{{ QUICK_CTX_LABELS[i] }}</button>
          </div>
        </div>
        <div>
          <label class="field-label">Max Output Tokens</label>
          <input v-model.number="form.max_output_tokens" type="number" placeholder="Default" class="settings-input">
        </div>
        <div>
          <label class="field-label">Input $/1M tokens</label>
          <input v-model.number="form.input_cost_per_mtok" type="number" step="0.001" placeholder="e.g. 2.00" class="settings-input">
        </div>
        <div>
          <label class="field-label">Output $/1M tokens</label>
          <input v-model.number="form.output_cost_per_mtok" type="number" step="0.001" placeholder="e.g. 8.00" class="settings-input">
        </div>
      </div>

      <!-- Skip Params -->
      <div class="mt-3">
        <span class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Skip Params: </span>
        <span v-if="form.skip_params.length === 0" class="text-[10px] text-zinc-700 font-body">none</span>
        <span v-for="p in form.skip_params" :key="p"
          class="text-[10px] font-mono text-zinc-400 px-1.5 py-0.5 rounded-sm inline-flex items-center gap-1 mr-1"
          style="background:rgba(255,255,255,0.04)"
        >
          {{ p }}
          <button @click="removeSkipParam(p)" class="text-zinc-600 hover:text-red-400">&times;</button>
        </span>
        <button @click="addSkipParam" class="text-[10px] font-mono text-zinc-500 hover:text-zinc-300 ml-2 px-1.5 py-0.5 rounded-sm" style="border:1px solid var(--border-subtle)">+ add</button>
      </div>

      <!-- Custom Fields -->
      <div class="mt-3">
        <span class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Custom Fields</span>
        <div class="space-y-1">
          <div v-if="customFields.length === 0" class="text-[10px] text-zinc-700 font-body">none</div>
          <div v-for="cf in customFields" :key="cf.key" class="flex items-center gap-2">
            <span class="text-[10px] font-mono text-zinc-500">{{ cf.key }}:</span>
            <input v-model="cf.value" class="px-2 py-1 rounded-sm text-[11px] font-mono text-zinc-300 flex-1" style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;">
            <button @click="removeCustomField(cf.key)" class="text-zinc-600 hover:text-red-400 text-xs">&times;</button>
          </div>
        </div>
        <button @click="addCustomField" class="text-[10px] font-mono text-zinc-500 hover:text-zinc-300 mt-1 px-1.5 py-0.5 rounded-sm" style="border:1px solid var(--border-subtle)">+ add field</button>
      </div>

      <!-- System Prompt -->
      <div class="mt-4 pt-3" style="border-top:1px solid var(--border-subtle)">
        <label class="field-label">System Prompt</label>
        <p class="text-[10px] text-zinc-700 font-body mb-2">Custom system prompt prepended to all benchmarks and evals for this model. Leave empty for default behavior.</p>
        <textarea
          v-model="form.system_prompt"
          rows="3"
          class="w-full px-3 py-2 rounded-sm text-xs font-mono text-zinc-200"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;resize:vertical;"
          placeholder="e.g. You are a helpful assistant. Always respond in JSON format."
        ></textarea>
      </div>
    </div>

    <!-- Save status -->
    <div v-if="saveMsg" class="mt-2 text-[11px] font-body" :style="`color:${saveOk ? 'var(--lime)' : 'var(--coral)'}`">{{ saveMsg }}</div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { apiFetch } from '../../utils/api.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'

const QUICK_CTX_SIZES = [4096, 8192, 16384, 32768, 65536, 131072, 200000]
const QUICK_CTX_LABELS = ['4K', '8K', '16K', '32K', '64K', '128K', '200K']
const STD_KEYS = new Set(['model_id', 'display_name', 'context_window', 'max_output_tokens', 'skip_params', 'input_cost_per_mtok', 'output_cost_per_mtok', 'system_prompt'])

const props = defineProps({
  model: { type: Object, required: true },
  providerKey: { type: String, required: true },
  prefix: { type: String, default: '' },
  discoveredModels: { type: Array, default: () => [] },
})

const emit = defineEmits(['save', 'delete'])

const { showToast } = useToast()
const { inputModal, multiFieldModal } = useModal()

const expanded = ref(false)
const saveMsg = ref('')
const saveOk = ref(true)

const form = reactive({
  model_id: props.model.model_id,
  display_name: props.model.display_name,
  context_window: props.model.context_window || 128000,
  max_output_tokens: props.model.max_output_tokens || null,
  input_cost_per_mtok: props.model.input_cost_per_mtok ?? null,
  output_cost_per_mtok: props.model.output_cost_per_mtok ?? null,
  skip_params: [...(props.model.skip_params || [])],
  system_prompt: props.model.system_prompt || '',
})

// Custom fields: anything not in STD_KEYS
const customFields = ref(
  Object.entries(props.model)
    .filter(([k]) => !STD_KEYS.has(k))
    .map(([key, value]) => ({ key, value }))
)

watch(() => props.model, (m) => {
  form.model_id = m.model_id
  form.display_name = m.display_name
  form.context_window = m.context_window || 128000
  form.max_output_tokens = m.max_output_tokens || null
  form.input_cost_per_mtok = m.input_cost_per_mtok ?? null
  form.output_cost_per_mtok = m.output_cost_per_mtok ?? null
  form.skip_params = [...(m.skip_params || [])]
  form.system_prompt = m.system_prompt || ''
  customFields.value = Object.entries(m)
    .filter(([k]) => !STD_KEYS.has(k))
    .map(([key, value]) => ({ key, value }))
}, { deep: true })

async function saveModel() {
  const customs = {}
  customFields.value.forEach(cf => { customs[cf.key] = cf.value })

  const pricing = {}
  if (form.input_cost_per_mtok != null && form.input_cost_per_mtok !== '') pricing.input_cost_per_mtok = parseFloat(form.input_cost_per_mtok)
  if (form.output_cost_per_mtok != null && form.output_cost_per_mtok !== '') pricing.output_cost_per_mtok = parseFloat(form.output_cost_per_mtok)

  const body = {
    provider_key: props.providerKey,
    model_id: props.model.model_id,
    new_model_id: form.model_id || props.model.model_id,
    display_name: form.display_name || '',
    context_window: parseInt(form.context_window) || null,
    max_output_tokens: parseInt(form.max_output_tokens) || null,
    skip_params: form.skip_params,
    system_prompt: form.system_prompt || null,
  }
  const allCustom = { ...customs, ...pricing }
  if (Object.keys(allCustom).length) body.custom_fields = allCustom

  try {
    const res = await apiFetch('/api/config/model', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (res.ok) {
      saveMsg.value = 'Saved'
      saveOk.value = true
      setTimeout(() => { saveMsg.value = '' }, 3000)
      emit('save')
    } else {
      const err = await res.json()
      saveMsg.value = err.error || 'Save failed'
      saveOk.value = false
    }
  } catch {
    saveMsg.value = 'Network error'
    saveOk.value = false
  }
}

async function addSkipParam() {
  const result = await inputModal('Add Skip Param', 'Parameter name (e.g., temperature)')
  const param = typeof result === 'object' ? result?.value : result
  if (!param || !param.trim()) return
  if (!form.skip_params.includes(param.trim())) {
    form.skip_params.push(param.trim())
  }
  await saveSkipParams()
}

function removeSkipParam(p) {
  form.skip_params = form.skip_params.filter(x => x !== p)
  saveSkipParams()
}

async function saveSkipParams() {
  try {
    await apiFetch('/api/config/model', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider_key: props.providerKey,
        model_id: props.model.model_id,
        skip_params: form.skip_params,
      }),
    })
    emit('save')
  } catch {
    showToast('Failed to save skip params', 'error')
  }
}

async function addCustomField() {
  const result = await multiFieldModal('Add Custom Field', [
    { key: 'name', label: 'Field Name', placeholder: 'e.g., pricing_tier' },
    { key: 'value', label: 'Value', placeholder: 'e.g., standard' },
  ])
  if (!result) return
  const name = result.name?.trim()
  const value = result.value
  if (!name) return
  customFields.value.push({ key: name, value })
  // Save immediately
  try {
    await apiFetch('/api/config/model', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider_key: props.providerKey,
        model_id: props.model.model_id,
        custom_fields: { [name]: value },
      }),
    })
    emit('save')
  } catch {
    showToast('Failed to add field', 'error')
  }
}

async function removeCustomField(key) {
  customFields.value = customFields.value.filter(cf => cf.key !== key)
  try {
    await apiFetch('/api/config/model', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider_key: props.providerKey,
        model_id: props.model.model_id,
        custom_fields: { [key]: null },
      }),
    })
    emit('save')
  } catch {
    showToast('Failed to remove field', 'error')
  }
}
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
  padding: 6px 16px;
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
</style>
