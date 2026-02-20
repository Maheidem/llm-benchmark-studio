<template>
  <div class="card rounded-md p-5 mb-6">
    <div class="flex items-center justify-between mb-3">
      <span class="section-label">Presets</span>
    </div>
    <div class="flex items-center gap-2">
      <select
        v-model="selectedPresetIdx"
        class="text-xs font-mono px-3 py-1.5 rounded-sm flex-1"
        style="background:var(--surface);border:1px solid var(--border-subtle);color:#E4E4E7;outline:none;"
      >
        <option value="">-- Presets --</option>
        <option v-for="(p, i) in presets" :key="i" :value="i">
          {{ p.name || 'Unnamed' }}
        </option>
      </select>
      <button
        @click="loadPreset"
        :disabled="selectedPresetIdx === ''"
        class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-colors"
        style="border:1px solid var(--border-subtle);color:var(--zinc-400);"
        :class="{ 'opacity-50 cursor-not-allowed': selectedPresetIdx === '' }"
      >Load</button>
      <button
        @click="savePreset"
        class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-colors"
        style="border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
      >Save</button>
      <button
        v-if="selectedPresetIdx !== ''"
        @click="deletePreset"
        class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-colors"
        style="border:1px solid rgba(255,59,92,0.2);color:var(--coral);"
      >Delete</button>
    </div>

    <!-- Save Dialog -->
    <div v-if="showSaveDialog" class="mt-3 flex items-center gap-2">
      <input
        v-model="presetName"
        type="text"
        placeholder="Preset name"
        class="text-xs font-mono px-3 py-1.5 rounded-sm flex-1"
        style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);color:#E4E4E7;outline:none;"
        @keyup.enter="confirmSave"
      >
      <button
        @click="confirmSave"
        class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
        style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
      >Save</button>
      <button
        @click="showSaveDialog = false"
        class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
        style="border:1px solid var(--border-subtle);color:var(--zinc-600);"
      >Cancel</button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { apiFetch } from '../../utils/api.js'
import { useToast } from '../../composables/useToast.js'

const props = defineProps({
  presets: { type: Array, default: () => [] },
  currentSearchSpace: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['load', 'update:presets'])

const { showToast } = useToast()
const selectedPresetIdx = ref('')
const showSaveDialog = ref(false)
const presetName = ref('')

function loadPreset() {
  if (selectedPresetIdx.value === '') return
  const idx = parseInt(selectedPresetIdx.value)
  const preset = props.presets[idx]
  if (!preset) return
  emit('load', preset.search_space || {})
  showToast(`Preset "${preset.name}" loaded`, 'success')
}

function savePreset() {
  if (Object.keys(props.currentSearchSpace).length === 0) {
    showToast('Enable at least one parameter before saving', 'error')
    return
  }
  showSaveDialog.value = true
  presetName.value = ''
}

async function confirmSave() {
  const name = presetName.value.trim()
  if (!name) {
    showToast('Name is required', 'error')
    return
  }

  const updated = [...props.presets]
  const existingIdx = updated.findIndex(p => p.name === name)
  const preset = { name, search_space: { ...props.currentSearchSpace } }

  if (existingIdx >= 0) {
    updated[existingIdx] = preset
  } else {
    if (updated.length >= 20) {
      showToast('Maximum 20 presets allowed', 'error')
      return
    }
    updated.push(preset)
  }

  await savePresetsToBackend(updated)
  emit('update:presets', updated)
  showSaveDialog.value = false
  showToast(`Preset "${name}" saved`, 'success')
}

async function deletePreset() {
  if (selectedPresetIdx.value === '') return
  const idx = parseInt(selectedPresetIdx.value)
  const name = props.presets[idx]?.name || 'Unnamed'

  const updated = [...props.presets]
  updated.splice(idx, 1)

  await savePresetsToBackend(updated)
  emit('update:presets', updated)
  selectedPresetIdx.value = ''
  showToast(`Preset "${name}" deleted`, 'success')
}

async function savePresetsToBackend(presets) {
  try {
    // First load current settings
    const getRes = await apiFetch('/api/settings/phase10')
    const current = getRes.ok ? await getRes.json() : {}

    // Merge presets into param_tuner section
    const data = {
      ...current,
      param_tuner: { ...(current.param_tuner || {}), presets },
    }

    await apiFetch('/api/settings/phase10', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
  } catch {
    showToast('Failed to save presets', 'error')
  }
}
</script>
