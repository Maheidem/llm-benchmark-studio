<template>
  <div class="card rounded-lg p-5">
    <!-- Header with label + count -->
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-3">
        <div class="pulse-dot"></div>
        <span class="text-sm text-zinc-300 font-body">{{ store.overallLabel || 'Running...' }}</span>
      </div>
      <span class="text-xs font-mono text-zinc-500">
        {{ store.overallProgress.completed }}/{{ store.overallProgress.total }}
        <span v-if="store.eta" class="ml-2 text-zinc-600">{{ store.eta }}</span>
      </span>
    </div>

    <!-- Overall progress bar -->
    <ProgressBar :percent="overallPct" :eta="store.eta" />

    <!-- Per-provider progress -->
    <div class="mt-5">
      <ProviderProgress :provider-progress="store.providerProgress" />
    </div>

    <!-- Skipped tiers -->
    <div v-if="store.skippedModels.length > 0" class="mt-4 space-y-1">
      <div
        v-for="(s, i) in store.skippedModels"
        :key="i"
        class="flex items-center gap-2 px-3 py-2 rounded-sm text-xs font-mono"
        style="background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.25);color:#FBBF24;"
      >
        <svg class="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
        </svg>
        <span>{{ s.model }}: <span class="text-zinc-400">{{ s.reason }}</span></span>
      </div>
    </div>

    <!-- Cancel button -->
    <div class="mt-4 flex justify-end">
      <button
        class="text-xs font-display tracking-wider uppercase px-4 py-2 rounded-sm transition-colors"
        style="color:var(--coral);background:rgba(255,59,92,0.08);border:1px solid rgba(255,59,92,0.2);"
        @click="store.cancelBenchmark()"
      >
        Cancel
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ProgressBar from '../ui/ProgressBar.vue'
import ProviderProgress from '../ui/ProviderProgress.vue'
import { useBenchmarkStore } from '../../stores/benchmark.js'

const store = useBenchmarkStore()

const overallPct = computed(() => {
  const { completed, total } = store.overallProgress
  return total > 0 ? (completed / total * 100) : 0
})
</script>
