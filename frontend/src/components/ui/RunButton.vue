<template>
  <button
    class="run-btn px-6 py-3 rounded flex items-center gap-2 text-sm"
    :disabled="disabled"
    @click="$emit('click')"
  >
    <template v-if="running">
      <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
      </svg>
      RUNNING...
    </template>
    <template v-else>
      <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/>
      </svg>
      <slot>{{ label }}</slot>
    </template>
  </button>
</template>

<script setup>
defineProps({
  label: { type: String, default: 'RUN BENCHMARK' },
  disabled: Boolean,
  running: Boolean,
})
defineEmits(['click'])
</script>
