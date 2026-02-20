<template>
  <div class="card rounded-md p-5" data-section="system-prompt">
    <div class="flex items-center justify-between mb-3 cursor-pointer" @click="expanded = !expanded">
      <div class="flex items-center gap-2">
        <span class="section-label" style="cursor:pointer;">System Prompt</span>
        <span
          v-if="hasAnyPrompt"
          class="text-[9px] px-1.5 py-0.5 rounded"
          style="background:rgba(34,197,94,0.15);color:#22C55E;"
        >Active</span>
      </div>
      <div class="flex items-center gap-2">
        <button
          v-if="hasAnyPrompt"
          @click.stop="clearAll"
          class="text-[10px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm"
          style="color:var(--coral);border:1px solid rgba(255,59,92,0.2);background:none;cursor:pointer;"
        >Clear</button>
        <svg
          class="w-4 h-4 text-zinc-600 transition-transform"
          :class="{ 'rotate-180': expanded }"
          fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"
        ><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>
      </div>
    </div>

    <div v-show="expanded">
      <!-- Tab bar for per-model prompts -->
      <div v-if="models.length > 1" class="flex flex-wrap gap-1.5 mb-2">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          @click="activeTab = tab.key"
          class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm transition-colors"
          :class="activeTab === tab.key
            ? 'text-zinc-200'
            : 'text-zinc-600 hover:text-zinc-400'"
          :style="activeTab === tab.key
            ? 'background:rgba(255,255,255,0.06);border:1px solid var(--border);'
            : 'background:transparent;border:1px solid transparent;'"
        >{{ tab.label }}</button>
      </div>

      <textarea
        :value="currentPrompt"
        @input="updatePrompt($event.target.value)"
        rows="5"
        class="w-full px-3 py-2 rounded-sm text-sm text-zinc-200"
        style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;font-family:'Space Mono',monospace;font-size:12px;resize:vertical;"
        placeholder="Enter a system prompt to prepend to eval calls..."
      ></textarea>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  models: { type: Array, default: () => [] },
  systemPrompts: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['update:systemPrompts'])

const expanded = ref(false)
const activeTab = ref('_global')

const tabs = computed(() => {
  const result = [{ key: '_global', label: 'Default (All)' }]
  for (const m of props.models) {
    const id = typeof m === 'string' ? m : (m.model_id || m.id || '')
    if (!id) continue
    const name = typeof m === 'string' ? m.split('/').pop() : (m.display_name || m.model_id || m.id || 'Model')
    result.push({ key: id, label: name })
  }
  return result
})

const currentPrompt = computed(() => {
  return props.systemPrompts[activeTab.value] || ''
})

const hasAnyPrompt = computed(() => {
  if (!props.systemPrompts) return false
  return Object.values(props.systemPrompts).some(v => v && v.trim())
})

function updatePrompt(value) {
  const updated = { ...props.systemPrompts, [activeTab.value]: value }
  emit('update:systemPrompts', updated)
}

function clearAll() {
  emit('update:systemPrompts', {})
  activeTab.value = '_global'
}

// Auto-expand if there's content
watch(() => props.systemPrompts, (sp) => {
  if (sp && Object.values(sp).some(v => v && v.trim())) {
    expanded.value = true
  }
}, { immediate: true })
</script>
