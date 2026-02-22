<template>
  <div class="max-w-5xl mx-auto px-4 py-8">
    <h1 class="font-display text-lg font-semibold text-zinc-200 uppercase tracking-wider mb-6">Settings</h1>
    <nav class="flex gap-2 mb-6">
      <router-link
        v-for="tab in tabs"
        :key="tab.path"
        :to="tab.path"
        :class="['settings-tab', { 'settings-tab-active': isActiveTab(tab.path) }]"
      >
        <span>{{ tab.icon }}</span>
        {{ tab.label }}
      </router-link>
    </nav>
    <router-view />
  </div>
</template>

<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()

const tabs = [
  { path: '/settings/keys', label: 'API Keys', icon: '\uD83D\uDD11' },
  { path: '/settings/providers', label: 'Providers', icon: '\u2699\uFE0F' },
  { path: '/settings/judge', label: 'Judge', icon: '\u2696\uFE0F' },
  { path: '/settings/tuning', label: 'Tuning', icon: '\uD83C\uDF9B\uFE0F' },
  { path: '/settings/profiles', label: 'Model Profiles', icon: '\uD83D\uDDC2\uFE0F' },
]

function isActiveTab(path) {
  return route.path === path || (path === '/settings/keys' && route.path === '/settings')
}
</script>
