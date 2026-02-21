<template>
  <div id="app-root">
    <AppHeader v-if="authStore.isAuthenticated && !isLandingRoute" />
    <router-view />
    <OnboardingWizard
      v-if="showOnboarding"
      :visible="showOnboarding"
      @complete="onOnboardingComplete"
    />
    <AppToasts />
    <AppModal />
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from './stores/auth.js'
import { useNotificationsStore } from './stores/notifications.js'
import { apiFetch } from './utils/api.js'
import AppHeader from './components/layout/AppHeader.vue'
import AppToasts from './components/ui/AppToasts.vue'
import AppModal from './components/ui/AppModal.vue'
import OnboardingWizard from './components/auth/OnboardingWizard.vue'

const route = useRoute()
const authStore = useAuthStore()
const notifStore = useNotificationsStore()
const isLandingRoute = computed(() => route.path === '/login')
const showOnboarding = ref(false)

function onOnboardingComplete() {
  showOnboarding.value = false
}

onMounted(async () => {
  await authStore.init()
  if (authStore.isAuthenticated) {
    notifStore.connect()
    // Check if user needs onboarding
    try {
      const res = await apiFetch('/api/onboarding/status')
      if (res.ok) {
        const data = await res.json()
        if (!data.completed) {
          showOnboarding.value = true
        }
      }
    } catch { /* ignore — onboarding is non-critical */ }
  }
})
</script>
