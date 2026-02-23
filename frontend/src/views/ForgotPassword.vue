<template>
  <div class="min-h-screen flex items-center justify-center" style="background: var(--bg-base, #09090b);">
    <div class="modal-box" style="max-width: 380px; width: 100%;">
      <!-- Logo -->
      <div class="flex items-center gap-2 mb-6">
        <div class="w-8 h-8 rounded-sm flex items-center justify-center font-display font-bold text-xs" style="background: var(--lime); color: #09090B;">
          B<span style="font-size:9px; opacity:0.6;">s</span>
        </div>
        <span class="font-display font-bold text-sm text-zinc-100 tracking-wide">
          BENCHMARK <span style="color: var(--lime)">STUDIO</span>
        </span>
      </div>

      <h2 class="section-title mb-1" style="font-size: 16px;">Reset your password</h2>
      <p class="text-zinc-400 mb-5" style="font-size: 12px;">
        Enter your email address and we'll send you a reset link.
      </p>

      <!-- Success state -->
      <div v-if="submitted" class="rounded p-4 mb-4 text-sm" style="background: rgba(132,204,22,0.1); border: 1px solid rgba(132,204,22,0.3); color: #a3e635;">
        If that email exists, a reset link has been sent. Check your inbox.
      </div>

      <!-- Form -->
      <form v-else @submit.prevent="handleSubmit">
        <div v-if="error" class="error-banner mb-4">{{ error }}</div>

        <label class="section-label block mb-1">Email</label>
        <input
          ref="emailRef"
          v-model="email"
          type="email"
          class="modal-input mb-4"
          required
          placeholder="you@example.com"
          autocomplete="email"
          :disabled="loading"
        />

        <button
          type="submit"
          :disabled="loading"
          class="modal-btn modal-btn-confirm w-full text-center mb-4"
          :style="{ opacity: loading ? 0.6 : 1 }"
        >
          {{ loading ? 'Sending...' : 'Send reset link' }}
        </button>
      </form>

      <div class="text-center" style="font-size: 12px;">
        <router-link to="/login" class="text-zinc-400 hover:text-zinc-200 transition-colors">
          Back to login
        </router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth.js'

const authStore = useAuthStore()
const email = ref('')
const error = ref('')
const loading = ref(false)
const submitted = ref(false)
const emailRef = ref(null)

onMounted(() => {
  emailRef.value?.focus()
})

async function handleSubmit() {
  error.value = ''
  loading.value = true
  try {
    await authStore.forgotPassword(email.value)
    submitted.value = true
  } catch (e) {
    error.value = e.message || 'Something went wrong. Please try again.'
  } finally {
    loading.value = false
  }
}
</script>
