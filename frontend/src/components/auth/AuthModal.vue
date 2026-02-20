<template>
  <div v-if="visible" class="modal-overlay" style="z-index:10000;" @click.self="$emit('close')">
    <div class="modal-box" style="max-width:380px;">
      <!-- Tab toggle -->
      <div class="flex gap-4 mb-5">
        <button
          :class="['tab', mode === 'login' ? 'tab-active' : '']"
          class="text-xs"
          @click="mode = 'login'; error = ''"
        >
          Login
        </button>
        <button
          :class="['tab', mode === 'register' ? 'tab-active' : '']"
          class="text-xs"
          @click="mode = 'register'; error = ''"
        >
          Register
        </button>
      </div>

      <!-- Error display -->
      <div v-if="error" class="error-banner mb-4">{{ error }}</div>

      <!-- Form -->
      <form @submit.prevent="handleSubmit">
        <label class="section-label block mb-1">Email</label>
        <input
          ref="emailRef"
          v-model="email"
          type="email"
          class="modal-input mb-3"
          required
          placeholder="you@example.com"
          autocomplete="email"
        />

        <label class="section-label block mb-1">Password</label>
        <input
          v-model="password"
          type="password"
          class="modal-input mb-4"
          required
          minlength="8"
          placeholder="Min 8 characters"
          autocomplete="current-password"
        />

        <button
          type="submit"
          :disabled="loading"
          class="modal-btn modal-btn-confirm w-full text-center"
          :style="{ opacity: loading ? 0.6 : 1 }"
        >
          {{ loading ? 'Please wait...' : (mode === 'login' ? 'Login' : 'Create Account') }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { useAuthStore } from '../../stores/auth.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
  initialMode: { type: String, default: 'login' },
})

const emit = defineEmits(['close', 'authenticated'])

const authStore = useAuthStore()
const mode = ref(props.initialMode)
const email = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const emailRef = ref(null)

watch(() => props.initialMode, (val) => {
  mode.value = val
})

watch(() => props.visible, async (val) => {
  if (val) {
    error.value = ''
    await nextTick()
    emailRef.value?.focus()
  }
})

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    if (mode.value === 'login') {
      await authStore.login(email.value, password.value)
    } else {
      await authStore.register(email.value, password.value)
    }
    email.value = ''
    password.value = ''
    emit('authenticated')
  } catch (e) {
    error.value = e.message || 'Connection error'
  } finally {
    loading.value = false
  }
}
</script>
