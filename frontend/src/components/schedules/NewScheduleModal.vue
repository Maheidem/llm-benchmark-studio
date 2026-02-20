<template>
  <Teleport to="body">
    <div class="modal-overlay" @click.self="$emit('close')">
      <div class="modal-box" style="max-width: 600px; max-height: 85vh; overflow-y: auto">
        <div class="modal-title">{{ editId ? 'Edit Schedule' : 'New Schedule' }}</div>

        <div class="space-y-4">
          <!-- Name -->
          <div>
            <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Schedule Name</label>
            <input
              ref="nameInput"
              v-model="form.name"
              class="modal-input w-full"
              style="margin-bottom: 0"
              placeholder="e.g. Daily throughput check"
            />
          </div>

          <!-- Prompt -->
          <div>
            <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Prompt</label>
            <textarea
              v-model="form.prompt"
              rows="3"
              class="prompt-input w-full rounded-sm px-4 py-3"
              placeholder="Enter benchmark prompt..."
            ></textarea>
          </div>

          <!-- Interval + Max Tokens -->
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Interval</label>
              <select
                v-model.number="form.interval_hours"
                class="w-full px-3 py-2 rounded-sm text-sm font-mono text-zinc-200 outline-none"
                style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-subtle)"
              >
                <option v-for="i in intervalOptions" :key="i.value" :value="i.value">{{ i.label }}</option>
              </select>
            </div>
            <div>
              <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Max Tokens</label>
              <input
                v-model.number="form.max_tokens"
                type="number"
                class="w-full px-3 py-2 rounded-sm text-sm font-mono text-zinc-200 outline-none"
                style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-subtle)"
              />
            </div>
          </div>

          <!-- Temperature -->
          <div>
            <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Temperature</label>
            <input
              v-model.number="form.temperature"
              type="number"
              step="0.1"
              min="0"
              max="2"
              class="px-3 py-2 rounded-sm text-sm font-mono text-zinc-200 outline-none"
              style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-subtle); max-width: 120px"
            />
          </div>

          <!-- Model selection -->
          <div>
            <div class="flex items-center justify-between mb-2">
              <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Select Models</label>
              <div class="flex gap-2">
                <button
                  class="text-[10px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm text-[var(--lime)] border border-[rgba(191,255,0,0.2)]"
                  @click="selectAllModels"
                >All</button>
                <button
                  class="text-[10px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm text-zinc-600 border border-[var(--border-subtle)]"
                  @click="clearAllModels"
                >Clear</button>
              </div>
            </div>

            <div
              v-if="loadingConfig"
              class="text-zinc-600 text-xs py-2"
            >Loading models...</div>

            <div
              v-else
              class="flex flex-col gap-1 max-h-48 overflow-y-auto pr-1"
              style="scrollbar-width: thin"
            >
              <div
                v-for="group in providerGroups"
                :key="group.provider"
                class="provider-group"
                :style="{ borderColor: group.color.border }"
              >
                <div
                  class="provider-group-header"
                  @click="toggleProviderModels(group)"
                >
                  <div class="provider-group-dot" :style="{ background: group.color.text }"></div>
                  <span class="provider-group-label" :style="{ color: group.color.text }">{{ group.provider }}</span>
                  <span class="provider-group-count">{{ group.models.length }}</span>
                </div>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 pl-3">
                  <div
                    v-for="m in group.models"
                    :key="m.model_id"
                    :class="['model-card rounded-sm px-3 py-2 flex items-center gap-2', { selected: selectedModels.has(m.model_id) }]"
                    @click="toggleModel(m.model_id)"
                  >
                    <div class="check-dot flex-shrink-0"></div>
                    <div class="text-[12px] font-medium text-zinc-200 truncate">{{ m.display_name }}</div>
                  </div>
                </div>
              </div>
            </div>
            <div class="mt-1 text-[10px] font-mono text-zinc-600">{{ selectedModels.size }} models selected</div>
          </div>
        </div>

        <div class="modal-buttons mt-4">
          <button class="modal-btn modal-btn-cancel" @click="$emit('close')">Cancel</button>
          <button class="modal-btn modal-btn-confirm" @click="submit">
            {{ editId ? 'Save' : 'Create Schedule' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, reactive, onMounted, nextTick } from 'vue'
import { apiFetch } from '../../utils/api.js'
import { useToast } from '../../composables/useToast.js'
import { useProviderColors } from '../../composables/useProviderColors.js'

const props = defineProps({
  editId: { type: String, default: null },
  initialData: { type: Object, default: null },
})

const emit = defineEmits(['close', 'created'])

const { showToast } = useToast()
const { getColor } = useProviderColors()

const nameInput = ref(null)
const loadingConfig = ref(false)
const providerGroups = ref([])
const selectedModels = reactive(new Set())

const intervalOptions = [
  { value: 1, label: 'Every hour' },
  { value: 6, label: 'Every 6 hours' },
  { value: 12, label: 'Every 12 hours' },
  { value: 24, label: 'Every day' },
  { value: 168, label: 'Every week' },
]

const form = reactive({
  name: props.initialData?.name || '',
  prompt: props.initialData?.prompt || 'Write a short story about a robot learning to paint.',
  interval_hours: props.initialData?.interval_hours || 24,
  max_tokens: props.initialData?.max_tokens || 512,
  temperature: props.initialData?.temperature || 0.7,
})

function toggleModel(modelId) {
  if (selectedModels.has(modelId)) {
    selectedModels.delete(modelId)
  } else {
    selectedModels.add(modelId)
  }
}

function toggleProviderModels(group) {
  const allSelected = group.models.every(m => selectedModels.has(m.model_id))
  group.models.forEach(m => {
    if (allSelected) {
      selectedModels.delete(m.model_id)
    } else {
      selectedModels.add(m.model_id)
    }
  })
}

function selectAllModels() {
  providerGroups.value.forEach(g => g.models.forEach(m => selectedModels.add(m.model_id)))
}

function clearAllModels() {
  selectedModels.clear()
}

async function loadConfig() {
  loadingConfig.value = true
  try {
    const res = await apiFetch('/api/config')
    const config = await res.json()
    const groups = []
    for (const [provider, provData] of Object.entries(config.providers || {})) {
      const color = getColor(provider)
      const models = []
      // Handle both array-style and object-style model definitions
      const rawModels = provData.models || []
      for (const m of rawModels) {
        const modelId = provData.model_prefix
          ? `${provData.model_prefix}${m.id}`
          : m.id
        models.push({
          model_id: modelId,
          display_name: m.display_name || m.id,
        })
      }
      if (models.length) {
        groups.push({ provider, color, models })
      }
    }
    providerGroups.value = groups
  } catch {
    showToast('Failed to load model config', 'error')
  } finally {
    loadingConfig.value = false
  }
}

async function submit() {
  if (!form.name.trim()) {
    showToast('Please enter a schedule name', 'error')
    return
  }
  if (!form.prompt.trim()) {
    showToast('Please enter a prompt', 'error')
    return
  }
  if (!selectedModels.size) {
    showToast('Please select at least one model', 'error')
    return
  }

  const body = {
    name: form.name.trim(),
    prompt: form.prompt.trim(),
    models: Array.from(selectedModels),
    interval_hours: form.interval_hours,
    max_tokens: form.max_tokens,
    temperature: form.temperature,
  }

  try {
    const url = props.editId ? `/api/schedules/${props.editId}` : '/api/schedules'
    const method = props.editId ? 'PUT' : 'POST'
    const res = await apiFetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (res.ok) {
      emit('created')
    } else {
      const err = await res.json()
      showToast(err.error || err.detail || 'Failed to save schedule', 'error')
    }
  } catch {
    showToast('Network error saving schedule', 'error')
  }
}

onMounted(async () => {
  await loadConfig()
  // Pre-select models if editing
  if (props.initialData?.models) {
    props.initialData.models.forEach(id => selectedModels.add(id))
  }
  await nextTick()
  nameInput.value?.focus()
})
</script>
