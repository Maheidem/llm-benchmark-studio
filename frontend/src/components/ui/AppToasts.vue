<template>
  <div class="toast-container">
    <div
      v-for="toast in toasts"
      :key="toast.id"
      :class="['toast', toast.type ? 'toast-' + toast.type : '', toast.removing ? 'toast-out' : '']"
    >
      {{ toast.message }}
    </div>
  </div>
</template>

<script setup>
import { useToast } from '../../composables/useToast.js'
const { toasts } = useToast()
</script>
