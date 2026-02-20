<template>
  <div class="progress-track rounded-full overflow-hidden">
    <div class="progress-fill" :style="{ width: percent + '%' }"></div>
  </div>
</template>

<script setup>
defineProps({
  percent: { type: Number, default: 0 },
})
</script>
