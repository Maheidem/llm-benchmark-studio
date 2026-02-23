<template>
  <div class="min-h-screen flex items-center justify-center" style="background: var(--bg-base, #09090b);">
    <div class="text-center">
      <div v-if="error" class="modal-box" style="max-width: 380px; width: 100%;">
        <div class="error-banner mb-4">{{ error }}</div>
        <router-link to="/login" class="modal-btn modal-btn-confirm block w-full text-center">
          Back to login
        </router-link>
      </div>
      <div v-else class="text-zinc-400 text-sm">
        Signing you in...
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth.js'
import { useNotificationsStore } from '../stores/notifications.js'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const notifStore = useNotificationsStore()
const error = ref('')

onMounted(async () => {
  // Token is passed as query param: /oauth-callback?token=<jwt>
  const token = route.query.token
  const oauthError = route.query.error

  if (oauthError) {
    error.value = `Google login failed: ${oauthError.replace(/_/g, ' ')}`
    return
  }

  if (!token) {
    error.value = 'Authentication failed: no token received.'
    return
  }

  try {
    // Store the token and fetch user profile
    authStore.setTokensFromOAuth(token)
    await authStore.fetchUser()
    notifStore.connect()

    // Check onboarding status
    try {
      const res = await fetch('/api/onboarding/status', {
        headers: { 'Authorization': 'Bearer ' + token },
      })
      if (res.ok) {
        const data = await res.json()
        if (!data.completed) {
          router.replace('/benchmark')
          return
        }
      }
    } catch { /* ignore */ }

    router.replace('/benchmark')
  } catch {
    error.value = 'Authentication failed. Please try again.'
  }
})
</script>
