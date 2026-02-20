<template>
  <div class="card rounded-md overflow-hidden">
    <!-- Provider Header -->
    <div class="px-5 py-3 flex items-center justify-between" style="border-bottom:1px solid var(--border-subtle)">
      <div class="flex items-center gap-3">
        <span
          class="badge"
          :style="`background:${color.bg};color:${color.text};border:1px solid ${color.border}`"
        >{{ provider.display_name }}</span>
        <span class="text-zinc-600 text-xs font-body">{{ models.length }} model{{ models.length !== 1 ? 's' : '' }}</span>
        <span class="text-[9px] font-mono text-zinc-700">{{ provider.provider_key }}</span>
        <span
          v-if="prefix"
          class="text-[9px] font-mono px-1.5 py-0.5 rounded-sm"
          style="background:rgba(56,189,248,0.08);color:#38BDF8;border:1px solid rgba(56,189,248,0.2)"
        >prefix: {{ prefix }}/</span>
      </div>
      <div class="flex gap-2">
        <button
          @click="$emit('fetch', provider.provider_key)"
          class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300 transition-colors"
        >Fetch</button>
        <button
          @click="showEdit = !showEdit"
          class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300 transition-colors"
        >Edit</button>
        <button
          @click="$emit('delete', provider)"
          class="text-[10px] font-display tracking-wider uppercase text-zinc-600 hover:text-red-400 transition-colors"
        >Del</button>
      </div>
    </div>

    <!-- Provider Edit Form -->
    <div
      v-if="showEdit"
      class="px-5 py-3"
      style="border-bottom:1px solid var(--border-subtle);background:rgba(191,255,0,0.02)"
    >
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-2">
        <div>
          <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Display Name</label>
          <input v-model="editForm.display_name" class="settings-input">
        </div>
        <div>
          <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Model ID Prefix</label>
          <input v-model="editForm.model_id_prefix" placeholder="Optional" class="settings-input">
        </div>
        <div>
          <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">API Base</label>
          <input v-model="editForm.api_base" placeholder="https://..." class="settings-input">
        </div>
        <div>
          <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">API Key Name</label>
          <input v-model="editForm.api_key_env" placeholder="e.g. OPENAI_API_KEY" class="settings-input">
        </div>
      </div>
      <div class="flex items-center gap-3">
        <button @click="saveProvider" class="lime-btn">Save Provider</button>
        <span v-if="saveStatus" class="text-[11px] font-body" :style="`color:${saveStatus.ok ? 'var(--lime)' : 'var(--coral)'}`">{{ saveStatus.msg }}</span>
      </div>
    </div>

    <!-- Models -->
    <div class="divide-y" style="border-color:var(--border-subtle)">
      <ModelCardSettings
        v-for="m in models"
        :key="m.model_id"
        :model="m"
        :provider-key="provider.provider_key"
        :prefix="prefix"
        :discovered-models="discoveredModels"
        @save="$emit('refresh')"
        @delete="(model) => $emit('deleteModel', { providerKey: provider.provider_key, model })"
      />
    </div>

    <!-- Add Model -->
    <div class="px-5 py-3" style="border-top:1px solid var(--border-subtle)">
      <button @click="showAddModel = !showAddModel" class="lime-btn">+ Add Model</button>
      <div v-if="showAddModel" class="mt-3">
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-2">
          <div>
            <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Model ID</label>
            <div class="flex items-center gap-0">
              <span
                v-if="prefix"
                class="text-[11px] font-mono px-2 py-2 rounded-l-sm flex-shrink-0"
                style="background:rgba(56,189,248,0.08);color:#38BDF8;border:1px solid rgba(56,189,248,0.2);border-right:none;"
              >{{ prefix }}/</span>
              <input
                v-model="addModelForm.id"
                :placeholder="prefix ? 'model-name' : 'provider/model-name'"
                :class="['settings-input', prefix ? 'rounded-l-none' : '']"
                list="dl-add-model"
                @focus="$emit('loadDiscovery', provider.provider_key)"
                @input="autoDeriveName"
              >
              <datalist id="dl-add-model">
                <option v-for="dm in discoveredModels" :key="dm.id" :value="dm.id">{{ dm.display_name }}</option>
              </datalist>
            </div>
          </div>
          <div>
            <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Display Name</label>
            <input v-model="addModelForm.display_name" placeholder="Auto from ID" class="settings-input">
          </div>
          <div>
            <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Context Window</label>
            <input v-model.number="addModelForm.context_window" type="number" class="settings-input">
          </div>
        </div>
        <button @click="addModel" class="lime-btn">Add</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { apiFetch } from '../../utils/api.js'
import { useToast } from '../../composables/useToast.js'
import { getColor } from '../../utils/constants.js'
import ModelCardSettings from './ModelCardSettings.vue'

const props = defineProps({
  provider: { type: Object, required: true },
  discoveredModels: { type: Array, default: () => [] },
})

const emit = defineEmits(['refresh', 'delete', 'deleteModel', 'fetch', 'loadDiscovery'])

const { showToast } = useToast()

const models = computed(() => props.provider.models || [])
const color = computed(() => getColor(props.provider.display_name))

const prefix = computed(() => {
  const explicit = props.provider.model_id_prefix
  if (explicit) return explicit
  return detectPrefix(models.value)
})

function detectPrefix(models) {
  if (!models || models.length === 0) return ''
  const ids = models.map(m => m.model_id || m.id || '')
  const firstSlash = ids[0].indexOf('/')
  if (firstSlash < 1) return ''
  const candidate = ids[0].substring(0, firstSlash)
  if (ids.every(id => id.startsWith(candidate + '/'))) return candidate
  return ''
}

const showEdit = ref(false)
const editForm = reactive({
  display_name: props.provider.display_name,
  model_id_prefix: props.provider.model_id_prefix || '',
  api_base: props.provider.api_base || '',
  api_key_env: props.provider.api_key_env || '',
})
const saveStatus = ref(null)

watch(() => props.provider, (p) => {
  editForm.display_name = p.display_name
  editForm.model_id_prefix = p.model_id_prefix || ''
  editForm.api_base = p.api_base || ''
  editForm.api_key_env = p.api_key_env || ''
}, { deep: true })

async function saveProvider() {
  try {
    const res = await apiFetch('/api/config/provider', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider_key: props.provider.provider_key,
        display_name: editForm.display_name,
        api_base: editForm.api_base,
        api_key_env: editForm.api_key_env,
        model_id_prefix: editForm.model_id_prefix,
      }),
    })
    if (res.ok) {
      saveStatus.value = { ok: true, msg: 'Saved' }
      setTimeout(() => { saveStatus.value = null }, 3000)
      emit('refresh')
    } else {
      const err = await res.json()
      saveStatus.value = { ok: false, msg: err.error || 'Failed' }
    }
  } catch {
    saveStatus.value = { ok: false, msg: 'Network error' }
  }
}

// Add Model
const showAddModel = ref(false)
const addModelForm = reactive({ id: '', display_name: '', context_window: 128000 })

function autoDeriveName() {
  if (!addModelForm.display_name) {
    const id = addModelForm.id
    addModelForm.display_name = id.includes('/') ? id.split('/').pop() : id
  }
  // Auto-fill from discovered models
  const match = props.discoveredModels.find(m => m.id === addModelForm.id)
  if (match) {
    addModelForm.display_name = match.display_name
  }
}

async function addModel() {
  const id = addModelForm.id.trim()
  if (!id) return
  try {
    const res = await apiFetch('/api/config/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider_key: props.provider.provider_key,
        id,
        display_name: addModelForm.display_name.trim() || '',
        context_window: addModelForm.context_window || 128000,
      }),
    })
    if (res.ok) {
      addModelForm.id = ''
      addModelForm.display_name = ''
      addModelForm.context_window = 128000
      showAddModel.value = false
      emit('refresh')
    } else {
      const err = await res.json()
      showToast(err.error || 'Failed to add model', 'error')
    }
  } catch {
    showToast('Network error', 'error')
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
