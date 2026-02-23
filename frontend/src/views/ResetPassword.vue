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

      <h2 class="section-title mb-1" style="font-size: 16px;">Set new password</h2>
      <p class="text-zinc-400 mb-5" style="font-size: 12px;">
        Enter your new password below.
      </p>

      <!-- No token -->
      <div v-if="!resetToken" class="error-banner mb-4">
        Invalid or missing reset link. Please request a new one.
        <div class="mt-2">
          <router-link to="/forgot-password" class="underline">Request reset link</router-link>
        </div>
      </div>

      <!-- Success state -->
      <div v-else-if="success">
        <div class="rounded p-4 mb-4 text-sm" style="background: rgba(132,204,22,0.1); border: 1px solid rgba(132,204,22,0.3); color: #a3e635;">
          Password updated successfully!
        </div>
        <router-link to="/login" class="modal-btn modal-btn-confirm block w-full text-center">
          Back to login
        </router-link>
      </div>

      <!-- Form -->
      <form v-else @submit.prevent="handleSubmit">
        <div v-if="error" class="error-banner mb-4">{{ error }}</div>

        <label class="section-label block mb-1">New password</label>
        <input
          ref="passwordRef"
          v-model="password"
          type="password"
          class="modal-input mb-3"
          required
          minlength="8"
          placeholder="Min 8 characters"
          autocomplete="new-password"
          :disabled="loading"
        />

        <label class="section-label block mb-1">Confirm password</label>
        <input
          v-model="confirmPassword"
          type="password"
          class="modal-input mb-4"
          required
          minlength="8"
          placeholder="Repeat your password"
          autocomplete="new-password"
          :disabled="loading"
        />

        <button
          type="submit"
          :disabled="loading"
          class="modal-btn modal-btn-confirm w-full text-center mb-4"
          :style="{ opacity: loading ? 0.6 : 1 }"
        >
          {{ loading ? 'Updating...' : 'Set new password' }}
        </button>
      </form>

      <div v-if="!success && resetToken" class="text-center" style="font-size: 12px;">
        <router-link to="/login" class="text-zinc-400 hover:text-zinc-200 transition-colors">
          Back to login
        </router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth.js'

const route = useRoute()
const authStore = useAuthStore()

const resetToken = ref(route.query.token || '')
const password = ref('')
const confirmPassword = ref('')
const error = ref('')
const loading = ref(false)
const success = ref(false)
const passwordRef = ref(null)

onMounted(() => {
  if (resetToken.value) {
    passwordRef.value?.focus()
  }
})

async function handleSubmit() {
  error.value = ''

  if (password.value !== confirmPassword.value) {
    error.value = 'Passwords do not match.'
    return
  }

  if (password.value.length < 8) {
    error.value = 'Password must be at least 8 characters.'
    return
  }

  loading.value = true
  try {
    await authStore.resetPassword(resetToken.value, password.value)
    success.value = true
  } catch (e) {
    error.value = e.message || 'Reset failed. The link may have expired.'
  } finally {
    loading.value = false
  }
}
</script>
