<template>
  <div class="flex flex-col gap-6">
    <!-- Stat cards -->
    <div v-if="hasResults" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <template v-if="!isStressMode">
        <!-- Standard stat cards -->
        <div class="stat-card card-accent rounded-sm p-4">
          <div class="section-label mb-2">Fastest</div>
          <div class="big-num text-2xl text-zinc-100">
            {{ winner.tokens_per_second.toFixed(1) }}
            <span class="text-xs font-normal" style="color:var(--lime)">tok/s</span>
          </div>
          <div class="text-[11px] text-zinc-600 mt-1 font-body">{{ winner.model }}</div>
        </div>

        <div class="stat-card card rounded-sm p-4" style="border-left: 3px solid #38BDF8">
          <div class="section-label mb-2">Best TTFT</div>
          <div class="big-num text-2xl text-zinc-100">
            {{ fastestTTFT.ttft_ms.toFixed(0) }}<span class="text-xs font-normal text-zinc-500">ms</span>
          </div>
          <div class="text-[11px] text-zinc-600 mt-1 font-body">{{ fastestTTFT.model }}</div>
        </div>

        <div class="stat-card card rounded-sm p-4" style="border-left: 3px solid #8B8B95">
          <div class="section-label mb-2">Models</div>
          <div class="big-num text-2xl text-zinc-100">{{ totalModels }}</div>
          <div class="text-[11px] text-zinc-600 mt-1 font-body">{{ totalRuns }} total runs</div>
        </div>

        <div
          class="stat-card card rounded-sm p-4"
          :style="'border-left: 3px solid ' + (grandTotalCost > 0 ? '#FB923C' : '#22C55E')"
        >
          <div class="section-label mb-2">Total Cost</div>
          <div class="big-num text-2xl text-zinc-100">
            {{ grandTotalCost > 0 ? '$' + grandTotalCost.toFixed(4) : '$0' }}
          </div>
          <div class="text-[11px] text-zinc-600 mt-1 font-body">
            {{ totalFails ? totalFails + ' failure(s)' : 'All nominal' }}
          </div>
        </div>
      </template>

      <template v-else>
        <!-- Stress test stat cards -->
        <div class="stat-card card-accent rounded-sm p-4">
          <div class="section-label mb-2">Best @ 0K</div>
          <template v-if="bestZero">
            <div class="big-num text-2xl text-zinc-100">
              {{ bestZero.tokens_per_second.toFixed(1) }}
              <span class="text-xs font-normal" style="color:var(--lime)">tok/s</span>
            </div>
            <div class="text-[11px] text-zinc-600 mt-1 font-body">{{ bestZero.model }}</div>
          </template>
          <div v-else class="text-zinc-600 text-sm font-body">N/A</div>
        </div>

        <div class="stat-card card rounded-sm p-4" style="border-left: 3px solid #38BDF8">
          <div class="section-label mb-2">Best @ {{ maxTierLabel }}</div>
          <template v-if="bestMax">
            <div class="big-num text-2xl text-zinc-100">
              {{ bestMax.tokens_per_second.toFixed(1) }}
              <span class="text-xs font-normal" style="color:#38BDF8">tok/s</span>
            </div>
            <div class="text-[11px] text-zinc-600 mt-1 font-body">{{ bestMax.model }}</div>
          </template>
          <div v-else class="text-zinc-600 text-sm font-body">N/A</div>
        </div>

        <div class="stat-card card rounded-sm p-4" style="border-left: 3px solid #8B8B95">
          <div class="section-label mb-2">Tiers Tested</div>
          <div class="big-num text-2xl text-zinc-100">{{ uniqueTiers }}</div>
          <div class="text-[11px] text-zinc-600 mt-1 font-body">{{ totalRuns }} total runs</div>
        </div>

        <div
          class="stat-card card rounded-sm p-4"
          :style="'border-left: 3px solid ' + (totalFails ? 'var(--coral)' : '#22C55E')"
        >
          <div class="section-label mb-2">Failures</div>
          <div class="big-num text-2xl" :class="totalFails ? 'text-red-400' : 'text-green-400'">
            {{ totalFails || 'None' }}
          </div>
          <div class="text-[11px] text-zinc-600 mt-1 font-body">
            {{ totalFails ? 'Check errors' : 'All nominal' }}
          </div>
        </div>
      </template>
    </div>

    <!-- All-failed banner -->
    <div v-if="allFailed" class="error-banner" style="flex-direction:column;align-items:flex-start;">
      <div>All benchmarks failed. Check your API keys and model configuration.</div>
      <div v-if="failedErrors.length" class="w-full mt-2 pt-2" style="border-top:1px solid rgba(255,59,92,0.2);">
        <div
          v-for="(err, i) in failedErrors"
          :key="i"
          class="text-[11px] font-mono mt-1 cursor-pointer"
          style="color:#FCA5A5;word-break:break-word;"
          @click="copyError(err)"
        >
          {{ err }}
        </div>
      </div>
    </div>

    <!-- Charts -->
    <template v-if="hasResults && !allFailed">
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ThroughputChart :results="results" :is-stress-mode="isStressMode" />
        <TTFTChart :results="results" :is-stress-mode="isStressMode" />
      </div>
      <ScatterChart :results="results" />
    </template>

    <!-- Export button -->
    <div v-if="hasResults && !allFailed" class="flex justify-end">
      <button
        class="text-[11px] font-display tracking-wider uppercase px-4 py-2 rounded-sm transition-colors text-zinc-500 hover:text-zinc-300"
        style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);"
        @click="exportCSV"
      >
        Export CSV
      </button>
    </div>

    <!-- Results table -->
    <ResultsTable v-if="hasResults" :results="results" :is-stress-mode="isStressMode" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ThroughputChart from './ThroughputChart.vue'
import TTFTChart from './TTFTChart.vue'
import ScatterChart from './ScatterChart.vue'
import ResultsTable from './ResultsTable.vue'
import { formatCtxSize } from '../../utils/helpers.js'

const props = defineProps({
  results: { type: Array, required: true },
  isStressMode: { type: Boolean, default: false },
})

const hasResults = computed(() => props.results.length > 0)
const allFailed = computed(() => hasResults.value && props.results.every(r => !r.success))

// Standard stats
const winner = computed(() => props.results[0] || {})
const fastestTTFT = computed(() => {
  return [...props.results].filter(a => a.success).sort((a, b) => a.ttft_ms - b.ttft_ms)[0] || {}
})
const totalModels = computed(() => props.results.length)
const totalRuns = computed(() => props.results.reduce((s, a) => s + a.runs, 0))
const totalFails = computed(() => props.results.reduce((s, a) => s + a.failures, 0))
const grandTotalCost = computed(() => props.results.reduce((s, a) => s + (a.total_cost || 0), 0))

// Stress stats
const bestZero = computed(() => {
  return props.results
    .filter(a => a.context_tokens === 0 && a.success)
    .sort((a, b) => b.tokens_per_second - a.tokens_per_second)[0]
})
const maxTier = computed(() => Math.max(...props.results.map(a => a.context_tokens)))
const maxTierLabel = computed(() => formatCtxSize(maxTier.value).replace(' ctx', ''))
const bestMax = computed(() => {
  return props.results
    .filter(a => a.context_tokens === maxTier.value && a.success)
    .sort((a, b) => b.tokens_per_second - a.tokens_per_second)[0]
})
const uniqueTiers = computed(() => new Set(props.results.map(a => a.context_tokens)).size)

const failedErrors = computed(() => {
  return props.results
    .filter(r => !r.success && r.error)
    .map(r => `${r.provider}/${r.model}: ${r.error}`)
})

function copyError(text) {
  navigator.clipboard.writeText(text).catch(() => {})
}

function exportCSV() {
  const agg = props.results
  if (!agg.length) return
  const headers = ['Provider', 'Model', 'Tok/s', 'TTFT (ms)', 'Input Tok/s', 'Duration (s)', 'Output Tokens', 'Status', 'Avg Cost', 'Total Cost']
  const rows = agg.map(r => [
    r.provider, r.model,
    r.success ? r.tokens_per_second.toFixed(2) : '',
    r.success ? r.ttft_ms.toFixed(0) : '',
    r.success && r.input_tokens_per_second > 0 ? Math.round(r.input_tokens_per_second) : '',
    r.success ? r.total_time_s.toFixed(3) : '',
    r.success ? r.output_tokens.toFixed(0) : '',
    r.success ? (r.failures ? 'partial' : 'ok') : 'fail',
    r.success && r.avg_cost > 0 ? r.avg_cost.toFixed(6) : '',
    r.success && r.total_cost > 0 ? r.total_cost.toFixed(6) : '',
  ])
  const csv = [headers, ...rows].map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'benchmark_results.csv'
  a.click()
  URL.revokeObjectURL(url)
}
</script>
