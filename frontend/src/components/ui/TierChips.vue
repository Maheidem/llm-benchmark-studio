<template>
  <div class="flex flex-wrap gap-2">
    <button
      v-for="t in tierOptions"
      :key="t.value"
      class="text-[11px] font-mono px-3 py-1.5 rounded-sm transition-all"
      :class="isSelected(t.value) ? '' : 'text-zinc-600 hover:text-zinc-400'"
      :style="isSelected(t.value)
        ? 'background:var(--lime-dim);color:var(--lime);border:1px solid rgba(191,255,0,0.3)'
        : 'background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle)'"
      @click="$emit('toggle', t.value)"
    >
      {{ t.label }}
    </button>
  </div>
  <div v-if="showStressBadge" class="mt-2">
    <span class="text-[10px] font-mono px-2 py-0.5 rounded-sm" style="background:rgba(56,189,248,0.1);color:#38BDF8;border:1px solid rgba(56,189,248,0.3);">
      STRESS TEST MODE
    </span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { TIER_OPTIONS } from '../../utils/constants.js'

const props = defineProps({
  selectedTiers: { type: Set, required: true },
  tierOptions: { type: Array, default: () => TIER_OPTIONS },
})

defineEmits(['toggle'])

function isSelected(value) {
  return props.selectedTiers.has(value)
}

const showStressBadge = computed(() => {
  return props.selectedTiers.size > 1 || (props.selectedTiers.size === 1 && !props.selectedTiers.has(0))
})
</script>
