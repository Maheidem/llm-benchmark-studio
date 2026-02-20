<template>
  <span
    class="badge"
    :style="{
      background: color.bg,
      border: '1px solid ' + color.border,
      color: color.text,
    }"
  >
    {{ name }}
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { getColor } from '../../utils/constants.js'

const props = defineProps({
  name: String,
  providerKey: String,
})

const color = computed(() => getColor(props.providerKey || props.name))
</script>
