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
          class="modal-input mb-1"
          required
          minlength="8"
          placeholder="Min 8 characters"
          autocomplete="current-password"
        />

        <!-- Forgot password link (login mode only) -->
        <div v-if="mode === 'login'" class="flex justify-end mb-4">
          <router-link
            to="/forgot-password"
            class="text-zinc-500 hover:text-zinc-300 transition-colors"
            style="font-size: 11px;"
            @click="$emit('close')"
          >
            Forgot password?
          </router-link>
        </div>
        <div v-else class="mb-4" />

        <button
          type="submit"
          :disabled="loading"
          class="modal-btn modal-btn-confirm w-full text-center"
          :style="{ opacity: loading ? 0.6 : 1 }"
        >
          {{ loading ? 'Please wait...' : (mode === 'login' ? 'Login' : 'Create Account') }}
        </button>
      </form>

      <!-- Google OAuth button -->
      <div v-if="googleAvailable !== false" class="mt-3">
        <div class="flex items-center gap-3 my-3">
          <div class="flex-1 h-px" style="background: var(--border-dim, #27272a);" />
          <span class="text-zinc-600" style="font-size: 11px;">or</span>
          <div class="flex-1 h-px" style="background: var(--border-dim, #27272a);" />
        </div>
        <button
          :disabled="googleLoading"
          class="modal-btn w-full flex items-center justify-center gap-2"
          style="background: #fff; color: #3c4043; border: 1px solid #dadce0; font-weight: 500;"
          :style="{ opacity: googleLoading ? 0.6 : 1 }"
          @click="handleGoogleLogin"
        >
          <!-- Google SVG logo -->
          <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
            <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
            <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332C2.438 15.983 5.482 18 9 18z" fill="#34A853"/>
            <path d="M3.964 10.71c-.18-.54-.282-1.117-.282-1.71s.102-1.17.282-1.71V4.958H.957C.347 6.173 0 7.548 0 9s.348 2.827.957 4.042l3.007-2.332z" fill="#FBBC05"/>
            <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0 5.482 0 2.438 2.017.957 4.958L3.964 6.29C4.672 4.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
          </svg>
          {{ googleLoading ? 'Redirecting...' : 'Sign in with Google' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted } from 'vue'
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
const googleLoading = ref(false)
const googleAvailable = ref(null) // null = unknown, true = available, false = not configured

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

onMounted(async () => {
  // Check if Google OAuth is configured
  try {
    const res = await fetch('/api/auth/google/authorize')
    googleAvailable.value = res.ok
  } catch {
    googleAvailable.value = false
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

async function handleGoogleLogin() {
  error.value = ''
  googleLoading.value = true
  try {
    await authStore.loginWithGoogle()
    // loginWithGoogle redirects the browser; no further action needed here
  } catch (e) {
    error.value = e.message || 'Google sign-in unavailable'
    googleLoading.value = false
  }
}
</script>
