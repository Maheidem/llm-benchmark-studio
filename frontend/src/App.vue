<template>
  <div id="app-root">
    <AppHeader v-if="authStore.isAuthenticated && !isLandingRoute" />
    <router-view />
    <AppToasts />
    <AppModal />
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from './stores/auth.js'
import { useNotificationsStore } from './stores/notifications.js'
import AppHeader from './components/layout/AppHeader.vue'
import AppToasts from './components/ui/AppToasts.vue'
import AppModal from './components/ui/AppModal.vue'

const route = useRoute()
const authStore = useAuthStore()
const notifStore = useNotificationsStore()
const isLandingRoute = computed(() => route.path === '/login')

onMounted(async () => {
  await authStore.init()
  if (authStore.isAuthenticated) {
    notifStore.connect()
  }
})
</script>
