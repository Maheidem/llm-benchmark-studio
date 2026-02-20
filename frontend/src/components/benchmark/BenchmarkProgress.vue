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
      </span>
    </div>

    <!-- Overall progress bar -->
    <ProgressBar :percent="overallPct" />

    <!-- Per-provider progress -->
    <div class="mt-5">
      <ProviderProgress :provider-progress="store.providerProgress" />
    </div>

    <!-- Skipped models -->
    <div v-if="store.skippedModels.length > 0" class="mt-4">
      <div
        v-for="(s, i) in store.skippedModels"
        :key="i"
        class="skipped-line"
      >
        &#x23ED; {{ s.model }}: skipped ({{ s.reason }})
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
