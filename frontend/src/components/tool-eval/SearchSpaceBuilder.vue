<template>
  <div class="card rounded-md p-5 mb-6">
    <div class="flex items-center justify-between mb-4">
      <span class="section-label">Search Space</span>
      <div class="flex items-center gap-2">
        <span class="text-xs font-mono text-zinc-600">{{ totalCombos }} combos</span>
        <span
          v-if="totalCombos > 0"
          class="text-[10px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm"
          :style="combosBadgeStyle"
        >{{ combosBadgeText }}</span>
      </div>
    </div>

    <div v-if="paramDefs.length === 0" class="text-xs text-zinc-600 font-body text-center py-4">
      Select models to configure parameters
    </div>

    <div v-else class="space-y-4">
      <div v-for="p in paramDefs" :key="p.name" :data-param-name="p.name" class="flex items-start gap-3">
        <!-- Toggle -->
        <label class="relative inline-flex items-center cursor-pointer mt-1.5">
          <input
            type="checkbox"
            :checked="enabledParams[p.name]"
            :disabled="p.locked"
            class="sr-only peer"
            @change="toggleParam(p.name)"
          >
          <div class="w-8 h-4 bg-zinc-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-zinc-400 after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-lime-900 peer-checked:after:bg-lime-400"></div>
        </label>

        <div class="flex-1">
          <div class="flex items-center gap-2 mb-2">
            <span class="text-xs text-zinc-400 font-body">{{ displayName(p.name) }}</span>
            <span v-if="p.supportedBy != null" class="text-[10px] font-mono px-1.5 py-0.5 rounded-sm"
              :style="supportBadgeStyle(p.supportedBy, p.totalModels)"
            >{{ p.supportedBy }}/{{ p.totalModels }}</span>
            <span v-if="p.locked" class="text-[10px] text-zinc-600 font-mono">locked: {{ p.lockedValue }}</span>
          </div>

          <!-- Float / Int range -->
          <template v-if="(p.type === 'float' || p.type === 'int') && !p.locked">
            <div class="grid grid-cols-3 gap-2 mb-2">
              <div>
                <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Min</label>
                <input
                  type="number"
                  :step="p.type === 'float' ? 0.01 : 1"
                  :value="getParamValue(p.name, 'min', p.min ?? 0)"
                  @input="setParamValue(p.name, 'min', $event.target.value, p.type)"
                  class="w-full px-2 py-1.5 rounded-sm text-xs font-mono text-zinc-200"
                  style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
                >
              </div>
              <div>
                <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Max</label>
                <input
                  type="number"
                  :step="p.type === 'float' ? 0.01 : 1"
                  :value="getParamValue(p.name, 'max', p.max ?? 1)"
                  @input="setParamValue(p.name, 'max', $event.target.value, p.type)"
                  class="w-full px-2 py-1.5 rounded-sm text-xs font-mono text-zinc-200"
                  style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
                >
              </div>
              <div>
                <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Step</label>
                <input
                  type="number"
                  :min="p.type === 'float' ? 0.01 : 1"
                  :step="p.type === 'float' ? 0.01 : 1"
                  :value="getParamValue(p.name, 'step', p.step ?? (p.type === 'float' ? 0.1 : 1))"
                  @input="setParamValue(p.name, 'step', $event.target.value, p.type)"
                  class="w-full px-2 py-1.5 rounded-sm text-xs font-mono text-zinc-200"
                  style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
                >
              </div>
            </div>
            <!-- Pills preview -->
            <div class="flex flex-wrap gap-1">
              <span v-for="v in getGeneratedValues(p.name, p.type)" :key="v"
                class="inline-block px-2 py-0.5 rounded-sm text-[10px] font-mono text-zinc-300"
                style="background:rgba(255,255,255,0.04);border:1px solid var(--border-subtle);"
              >{{ v }}</span>
            </div>
          </template>

          <!-- Enum type -->
          <template v-else-if="p.type === 'enum' && !p.locked">
            <div class="flex flex-wrap gap-4">
              <label v-for="v in (p.values || [])" :key="v" class="flex items-center gap-2 text-xs text-zinc-400 font-body cursor-pointer">
                <input
                  type="checkbox"
                  :checked="isEnumChecked(p.name, v)"
                  @change="toggleEnumValue(p.name, v)"
                  class="accent-lime-400"
                >
                {{ v }}
              </label>
            </div>
          </template>

          <!-- Bool type -->
          <template v-else-if="p.type === 'bool' && !p.locked">
            <span class="text-[10px] text-zinc-600 font-body">Values: true, false</span>
          </template>

          <!-- Locked display -->
          <template v-else-if="p.locked">
            <div class="text-xs font-mono text-zinc-500">{{ p.lockedValue }}</div>
          </template>
        </div>
      </div>
    </div>

    <!-- Combo breakdown -->
    <div v-if="totalCombos > 0" class="mt-4 pt-3" style="border-top:1px solid var(--border-subtle);">
      <div class="text-[10px] text-zinc-600 font-body">
        {{ breakdownText }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'

const props = defineProps({
  paramDefs: { type: Array, default: () => [] },
  modelValue: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['update:modelValue', 'update:totalCombos'])

// Enabled state per param
const enabledParams = reactive({})
// Values per param: { min, max, step } for range, or array for enum
const paramValues = reactive({})

// Initialize from paramDefs and modelValue
watch(() => props.paramDefs, (defs) => {
  const autoEnable = new Set(['temperature', 'tool_choice'])
  for (const p of defs) {
    if (!(p.name in enabledParams)) {
      enabledParams[p.name] = !p.locked && (autoEnable.has(p.name) || p.name in (props.modelValue || {}))
    }
    if (!(p.name in paramValues)) {
      if (p.name in (props.modelValue || {})) {
        paramValues[p.name] = props.modelValue[p.name]
      } else if (p.type === 'float' || p.type === 'int') {
        paramValues[p.name] = {
          min: p.min ?? 0,
          max: p.max ?? 1,
          step: p.step ?? (p.type === 'float' ? 0.1 : 1),
        }
      } else if (p.type === 'enum') {
        const defaultChecked = p.name === 'tool_choice' ? ['auto', 'required'] : [...(p.values || [])]
        paramValues[p.name] = defaultChecked
      } else if (p.type === 'bool') {
        paramValues[p.name] = [true, false]
      }
    }
  }
  emitSearchSpace()
}, { immediate: true })

// Watch modelValue for external changes (preset loading)
watch(() => props.modelValue, (newVal) => {
  if (!newVal || Object.keys(newVal).length === 0) return
  for (const [name, val] of Object.entries(newVal)) {
    enabledParams[name] = true
    paramValues[name] = val
  }
  // Disable params not in the preset
  for (const name of Object.keys(enabledParams)) {
    if (!(name in newVal)) {
      enabledParams[name] = false
    }
  }
}, { deep: true })

const DISPLAY_NAMES = {
  temperature: 'Temperature',
  top_p: 'Top P',
  top_k: 'Top K',
  tool_choice: 'Tool Choice',
  repetition_penalty: 'Repetition Penalty',
  min_p: 'Min P',
  frequency_penalty: 'Frequency Penalty',
  presence_penalty: 'Presence Penalty',
  max_tokens: 'Max Tokens',
}

function displayName(name) {
  return DISPLAY_NAMES[name] || name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function supportBadgeStyle(supported, total) {
  const ratio = total > 0 ? supported / total : 0
  if (ratio >= 1) return { background: 'rgba(191,255,0,0.1)', color: 'var(--lime)' }
  if (ratio >= 0.5) return { background: 'rgba(234,179,8,0.1)', color: '#EAB308' }
  return { background: 'rgba(255,59,92,0.1)', color: 'var(--coral)' }
}

function toggleParam(name) {
  enabledParams[name] = !enabledParams[name]
  emitSearchSpace()
}

function getParamValue(name, field, fallback) {
  const v = paramValues[name]
  if (v && typeof v === 'object' && !Array.isArray(v)) return v[field] ?? fallback
  return fallback
}

function setParamValue(name, field, value, type) {
  if (!paramValues[name] || typeof paramValues[name] !== 'object' || Array.isArray(paramValues[name])) {
    paramValues[name] = {}
  }
  paramValues[name][field] = type === 'int' ? parseInt(value) : parseFloat(value)
  emitSearchSpace()
}

function isEnumChecked(name, value) {
  const arr = paramValues[name]
  return Array.isArray(arr) && arr.includes(value)
}

function toggleEnumValue(name, value) {
  if (!Array.isArray(paramValues[name])) paramValues[name] = []
  const idx = paramValues[name].indexOf(value)
  if (idx >= 0) {
    paramValues[name].splice(idx, 1)
  } else {
    paramValues[name].push(value)
  }
  emitSearchSpace()
}

function generateValues(min, max, step) {
  const vals = []
  if (step <= 0 || min > max) return vals
  for (let v = min; v <= max + step * 0.001; v += step) {
    vals.push(Math.round(v * 1000) / 1000)
  }
  return vals
}

function getGeneratedValues(name, type) {
  if (!enabledParams[name]) return []
  const v = paramValues[name]
  if (!v || typeof v !== 'object' || Array.isArray(v)) return []
  const min = v.min ?? 0
  const max = v.max ?? 1
  const step = v.step ?? (type === 'float' ? 0.1 : 1)
  const vals = generateValues(min, max, step)
  return type === 'int' ? vals.map(x => Math.round(x)) : vals
}

// Compute total combos
const totalCombos = computed(() => {
  const dims = []
  for (const p of props.paramDefs) {
    if (!enabledParams[p.name]) continue
    if (p.type === 'float' || p.type === 'int') {
      const vals = getGeneratedValues(p.name, p.type)
      if (vals.length > 0) dims.push(vals.length)
    } else if (p.type === 'enum') {
      const arr = paramValues[p.name]
      if (Array.isArray(arr) && arr.length > 0) dims.push(arr.length)
    } else if (p.type === 'bool') {
      dims.push(2)
    }
  }
  return dims.length > 0 ? dims.reduce((a, b) => a * b, 1) : 0
})

watch(totalCombos, (val) => emit('update:totalCombos', val))

const breakdownText = computed(() => {
  const parts = []
  for (const p of props.paramDefs) {
    if (!enabledParams[p.name]) continue
    let count = 0
    if (p.type === 'float' || p.type === 'int') {
      count = getGeneratedValues(p.name, p.type).length
    } else if (p.type === 'enum') {
      const arr = paramValues[p.name]
      count = Array.isArray(arr) ? arr.length : 0
    } else if (p.type === 'bool') {
      count = 2
    }
    if (count > 0) parts.push(`${count} ${p.name}`)
  }
  return parts.length > 1 ? '= ' + parts.map(p => p.split(' ')[0]).join(' x ') : ''
})

const combosBadgeText = computed(() => {
  const c = totalCombos.value
  if (c < 20) return 'QUICK'
  if (c < 50) return 'MODERATE'
  if (c <= 100) return 'LARGE'
  return 'WARNING'
})

const combosBadgeStyle = computed(() => {
  const c = totalCombos.value
  if (c < 20) return { background: 'rgba(191,255,0,0.1)', color: 'var(--lime)' }
  if (c < 50) return { background: 'rgba(234,179,8,0.1)', color: '#EAB308' }
  if (c <= 100) return { background: 'rgba(249,115,22,0.1)', color: '#F97316' }
  return { background: 'rgba(255,59,92,0.1)', color: 'var(--coral)' }
})

function emitSearchSpace() {
  const space = {}
  for (const p of props.paramDefs) {
    if (!enabledParams[p.name]) continue
    if (p.type === 'float' || p.type === 'int') {
      const v = paramValues[p.name]
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        space[p.name] = { min: v.min ?? 0, max: v.max ?? 1, step: v.step ?? 0.1 }
      }
    } else if (p.type === 'enum') {
      const arr = paramValues[p.name]
      if (Array.isArray(arr) && arr.length > 0) space[p.name] = [...arr]
    } else if (p.type === 'bool') {
      space[p.name] = [true, false]
    }
  }
  emit('update:modelValue', space)
}

// Expose for parent to call
defineExpose({ enabledParams, paramValues })
</script>
