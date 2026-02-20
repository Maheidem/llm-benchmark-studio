<template>
  <div class="max-w-7xl mx-auto px-4 py-8">
    <h1 class="font-display text-lg font-semibold text-zinc-200 uppercase tracking-wider mb-6">Analytics</h1>
    <nav class="flex gap-2 mb-6 border-b border-zinc-800">
      <router-link
        v-for="tab in tabs"
        :key="tab.path"
        :to="tab.path"
        :class="['tab px-4', { 'tab-active': isActive(tab.path) }]"
      >
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
  { path: '/analytics/leaderboard', label: 'Leaderboard' },
  { path: '/analytics/compare', label: 'Compare' },
  { path: '/analytics/trends', label: 'Trends' },
]

function isActive(path) {
  return route.path === path || (path === '/analytics/leaderboard' && route.path === '/analytics')
}
</script>
