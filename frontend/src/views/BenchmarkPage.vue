<template>
  <div class="max-w-7xl mx-auto px-6 py-8">
    <!-- Loading state -->
    <div v-if="configStore.loading" class="text-center py-16">
      <div class="text-zinc-500 text-sm">Loading configuration...</div>
    </div>

    <!-- Error banner -->
    <div v-else-if="initError" class="error-banner mb-6">
      <div>Failed to load configuration. Check your connection and refresh.</div>
      <button @click="loadAll">Retry</button>
    </div>

    <template v-else-if="configStore.config">
      <!-- Model selection -->
      <div class="mb-6">
        <div class="flex items-center justify-between mb-4">
          <h2 class="section-label">Select Models</h2>
          <span class="text-xs font-mono text-zinc-600">
            {{ benchmarkStore.selectedCount }} selected
          </span>
        </div>
        <ModelGrid
          :providers="configStore.providers"
          :selected-models="benchmarkStore.selectedModels"
          @toggle="benchmarkStore.toggleModel"
          @select-all="benchmarkStore.selectAll"
          @select-none="benchmarkStore.selectNone"
          @toggle-provider="benchmarkStore.toggleProviderModels"
        />
      </div>

      <!-- Configuration panel -->
      <div class="mb-6">
        <BenchmarkConfig />
      </div>

      <!-- Run button -->
      <div class="mb-6 flex items-center gap-4">
        <RunButton
          :disabled="benchmarkStore.isRunning || benchmarkStore.selectedCount === 0"
          :running="benchmarkStore.isRunning"
          @click="runBenchmark"
        />
        <span v-if="benchmarkStore.selectedCount === 0" class="text-xs text-zinc-600">
          Select at least one model to run
        </span>
      </div>

      <!-- Error banner (runtime) -->
      <div v-if="benchmarkStore.errorBanner" class="error-banner mb-6">
        <div>{{ benchmarkStore.errorBanner }}</div>
        <button v-if="benchmarkStore.lastBenchmarkBody" @click="retryBenchmark">
          Retry
        </button>
      </div>

      <!-- Progress section -->
      <div v-if="benchmarkStore.isRunning" class="mb-6">
        <BenchmarkProgress />
      </div>

      <!-- Results section -->
      <div v-if="showResults" class="mb-6">
        <BenchmarkResults
          :results="benchmarkStore.aggregatedResults"
          :is-stress-mode="benchmarkStore.isStressMode"
        />
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useConfigStore } from '../stores/config.js'
import { useBenchmarkStore } from '../stores/benchmark.js'
import { useToast } from '../composables/useToast.js'
import { useWebSocket } from '../composables/useWebSocket.js'
import { getToken } from '../utils/api.js'
import ModelGrid from '../components/ui/ModelGrid.vue'
import RunButton from '../components/ui/RunButton.vue'
import BenchmarkConfig from '../components/benchmark/BenchmarkConfig.vue'
import BenchmarkProgress from '../components/benchmark/BenchmarkProgress.vue'
import BenchmarkResults from '../components/benchmark/BenchmarkResults.vue'

const configStore = useConfigStore()
const benchmarkStore = useBenchmarkStore()
const { showToast } = useToast()

const initError = ref(false)

const showResults = computed(() => {
  return !benchmarkStore.isRunning && benchmarkStore.currentResults.length > 0
})

// WebSocket for benchmark progress
const { connect, disconnect } = useWebSocket(
  () => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = getToken()
    return `${proto}//${location.host}/ws?token=${encodeURIComponent(token)}`
  },
  {
    onMessage(msg) {
      // Forward benchmark job events to store
      benchmarkStore.handleJobEvent(msg)

      // Handle sync to restore running benchmarks
      if (msg.type === 'sync') {
        for (const job of (msg.active_jobs || [])) {
          if (job.job_type === 'benchmark' && ['running', 'pending', 'queued'].includes(job.status)) {
            benchmarkStore.restoreRunningJob(job)
          }
        }
      }
    },
  }
)

async function loadAll() {
  initError.value = false
  try {
    await configStore.loadConfig()
    benchmarkStore.applyDefaults(configStore.config)
    benchmarkStore.selectAllFromConfig(configStore.config)
    configStore.loadParamsRegistry() // async, non-blocking
  } catch (e) {
    console.error('Failed to load config:', e)
    initError.value = true
  }
}

async function runBenchmark() {
  try {
    const data = await benchmarkStore.startBenchmark()
    showToast('Benchmark submitted', 'success')
  } catch (e) {
    showToast(e.message || 'Benchmark failed', 'error')
  }
}

function retryBenchmark() {
  benchmarkStore.errorBanner = null
  if (benchmarkStore.lastBenchmarkBody) {
    benchmarkStore.startBenchmark(benchmarkStore.lastBenchmarkBody).catch(e => {
      showToast(e.message || 'Retry failed', 'error')
    })
  }
}

onMounted(() => {
  loadAll()
  connect()
})

onUnmounted(() => {
  disconnect()
})
</script>
