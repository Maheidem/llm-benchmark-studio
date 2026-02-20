<template>
  <header class="border-b px-6 py-4" style="border-color: var(--border-subtle); position: sticky; top: 0; z-index: 10; background: rgba(9,9,11,0.85); backdrop-filter: blur(12px);">
    <!-- WS connection warning banner -->
    <div
      v-if="notifStore.wsBannerVisible"
      class="text-[11px] font-body px-4 py-2"
      style="background:rgba(234,179,8,0.1);color:#EAB308;border-bottom:1px solid rgba(234,179,8,0.2);position:absolute;top:100%;left:0;right:0;"
    >
      Real-time connection lost. Retrying... If this persists, check your network.
    </div>

    <div class="max-w-7xl mx-auto flex items-center justify-between">
      <!-- Logo -->
      <div class="flex items-center gap-4">
        <div class="w-9 h-9 rounded-sm flex items-center justify-center font-display font-bold text-sm" style="background: var(--lime); color: #09090B;">
          B<span style="font-size:10px; opacity:0.6;">s</span>
        </div>
        <div>
          <h1 class="font-display font-bold text-base text-zinc-100 tracking-wide">
            BENCHMARK <span style="color: var(--lime)">STUDIO</span>
          </h1>
          <p class="text-[10px] tracking-[0.2em] uppercase text-zinc-600 mt-0.5">Measure &middot; Compare &middot; Optimize</p>
        </div>
      </div>

      <!-- Nav + Notification + User -->
      <div class="flex items-center gap-6">
        <router-link to="/benchmark" class="tab" active-class="tab-active">Benchmark</router-link>
        <router-link to="/tool-eval" class="tab" active-class="tab-active">Tool Eval</router-link>
        <router-link to="/history" class="tab" active-class="tab-active">History</router-link>
        <router-link to="/analytics" class="tab" active-class="tab-active">Analytics</router-link>
        <router-link to="/schedules" class="tab" active-class="tab-active">Schedules</router-link>
        <router-link to="/settings" class="tab" active-class="tab-active">Settings</router-link>
        <router-link v-if="authStore.isAdmin" to="/admin" class="tab" active-class="tab-active">Admin</router-link>

        <!-- Notification Widget -->
        <NotificationWidget />

        <!-- User Menu -->
        <div class="flex items-center gap-3 ml-4 pl-4" style="border-left:1px solid var(--border-subtle);">
          <span class="text-[11px] text-zinc-500 font-mono">{{ authStore.user?.email }}</span>
          <span v-if="authStore.isAdmin" class="text-[9px] font-mono px-1.5 py-0.5 rounded-sm" style="background:rgba(191,255,0,0.08);color:var(--lime);border:1px solid rgba(191,255,0,0.2);">admin</span>
          <button
            @click="handleLogout"
            class="text-[10px] font-display tracking-wider uppercase text-zinc-600 hover:text-zinc-300 px-2 py-1 rounded"
            style="border:1px solid var(--border-subtle)"
          >
            Logout
          </button>
        </div>
      </div>
    </div>
  </header>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth.js'
import { useNotificationsStore } from '../../stores/notifications.js'
import NotificationWidget from './NotificationWidget.vue'

const router = useRouter()
const authStore = useAuthStore()
const notifStore = useNotificationsStore()

function handleLogout() {
  notifStore.disconnect()
  authStore.logout()
  router.push('/login')
}
</script>
