<template>
  <div>
    <div class="progress-track rounded-full overflow-hidden">
      <div class="progress-fill" :style="{ width: percent + '%' }"></div>
    </div>
    <div v-if="eta" class="text-[10px] font-mono text-zinc-500 mt-1 text-right">
      {{ eta }}
    </div>
  </div>
</template>

<script setup>
defineProps({
  percent: { type: Number, default: 0 },
  eta: { type: String, default: '' },
})
</script>
