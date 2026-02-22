<template>
  <div class="max-w-7xl mx-auto px-6 py-8">
    <div class="flex items-center justify-between mb-6">
      <h2 class="section-label">Benchmark History</h2>
      <div class="flex items-center gap-3">
        <button
          @click="refreshHistory"
          :disabled="loading"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-opacity"
          :class="loading ? 'opacity-50 cursor-not-allowed' : ''"
          style="border:1px solid var(--border-subtle);color:var(--zinc-400);"
          title="Refresh history"
        >
          <span v-if="loading">...</span>
          <span v-else>Refresh</span>
        </button>
        <input
          v-model="searchQuery"
          type="text"
          placeholder="Search history..."
          class="px-3 py-1.5 rounded-sm text-xs font-mono text-zinc-200"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;width:200px;"
          @focus="$event.target.style.borderColor = 'rgba(191,255,0,0.3)'"
          @blur="$event.target.style.borderColor = 'var(--border-subtle)'"
        >
      </div>
    </div>

    <!-- Compare bar -->
    <div
      v-if="selectedRuns.size >= 2"
      class="card rounded-sm px-5 py-3 mb-4 flex items-center justify-between"
      style="border-color:rgba(56,189,248,0.3)"
    >
      <span class="text-xs font-body text-zinc-300">{{ selectedRuns.size }} runs selected for comparison</span>
      <div class="flex gap-3">
        <button @click="compareRuns" class="lime-btn text-[10px]">Compare</button>
        <button @click="selectedRuns = new Set()" class="text-[10px] font-display tracking-wider uppercase text-zinc-600 hover:text-zinc-300 transition-colors">Clear</button>
      </div>
    </div>

    <!-- Compare result -->
    <div v-if="compareResult" class="mb-6">
      <div class="card rounded-md overflow-hidden">
        <div class="px-5 py-3 flex items-center justify-between" style="border-bottom:1px solid var(--border-subtle)">
          <span class="section-label">Cross-Run Comparison</span>
          <button @click="compareResult = null" class="text-[11px] font-display tracking-wider uppercase text-zinc-600 hover:text-zinc-300 px-2 py-1">Close</button>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-xs results-table">
            <thead>
              <tr style="border-bottom:1px solid var(--border-subtle)">
                <th class="px-4 py-3 text-left section-label">Model</th>
                <th v-for="label in compareResult.labels" :key="label" class="px-4 py-3 text-right section-label">{{ label }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in compareResult.rows" :key="row.key" style="border-top:1px solid var(--border-subtle)">
                <td class="px-4 py-2.5">
                  <span class="badge mr-2" :style="`background:${row.color.bg};color:${row.color.text};border:1px solid ${row.color.border};font-size:9px`">{{ row.provider }}</span>
                  <span class="text-zinc-300 font-body text-[12px]">{{ row.model }}</span>
                </td>
                <td v-for="(v, vi) in row.values" :key="vi" class="px-4 py-2.5 text-right font-mono" :class="getCellColor(v, row.values)">
                  {{ v === null ? '-' : (v.toFixed ? v.toFixed(1) : v) + ' tok/s' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="text-zinc-600 text-sm font-body">Loading history...</div>

    <!-- Empty state -->
    <div v-else-if="filteredHistory.length === 0 && history.length === 0" class="card rounded-sm p-10 text-center">
      <p class="text-zinc-600 font-body">No benchmark history yet.</p>
      <p class="text-zinc-700 text-xs mt-1 font-body">Run your first benchmark to see results here.</p>
    </div>

    <!-- History list -->
    <div v-else class="space-y-3">
      <div
        v-for="(h, idx) in filteredHistory"
        :key="h.id || idx"
        class="card rounded-sm p-5 fade-in cursor-pointer hover:bg-white/[0.02] transition-colors"
        :style="`animation-delay: ${idx * 40}ms`"
        @click="openDetail(h)"
      >
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-3">
            <!-- Compare checkbox -->
            <button
              v-if="h.id"
              @click.stop="toggleRunSelection(h.id)"
              class="w-4 h-4 rounded-sm border flex items-center justify-center transition-all flex-shrink-0"
              :style="selectedRuns.has(h.id)
                ? 'border-color:#38BDF8;background:rgba(56,189,248,0.15)'
                : 'border-color:var(--border-subtle)'"
              title="Select for comparison"
            >
              <svg v-if="selectedRuns.has(h.id)" class="w-2.5 h-2.5" fill="none" stroke="#38BDF8" stroke-width="3" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
              </svg>
            </button>
            <span class="text-[11px] text-zinc-600 font-mono">{{ formatTimestamp(h.timestamp) }}</span>
            <span v-if="getWinner(h)" class="badge" style="background:var(--lime-dim);color:var(--lime);border:1px solid rgba(191,255,0,0.2)">P1: {{ getWinner(h).model }}</span>
            <span
              v-if="h.context_tiers && h.context_tiers.length"
              class="badge"
              style="background:rgba(161,161,170,0.1);color:rgb(161,161,170);border:1px solid rgba(161,161,170,0.2)"
              :title="'Context tiers tested'"
            >CTX {{ h.context_tiers.map(formatTier).join(' / ') }}</span>
          </div>
          <div class="flex items-center gap-3">
            <span v-if="getWinner(h)" class="font-mono text-sm" style="color:var(--lime)">{{ getWinner(h).avg_tokens_per_second }} tok/s</span>
            <!-- Re-run button -->
            <button
              v-if="h.id"
              @click.stop="rerunBenchmark(h)"
              class="text-zinc-500 hover:text-lime-400 transition-colors"
              title="Re-run this benchmark"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
              </svg>
            </button>
            <button v-if="h.id" @click.stop="deleteRun(h.id)" class="text-zinc-700 hover:text-red-400 transition-colors" title="Delete run">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
              </svg>
            </button>
          </div>
        </div>

        <!-- Results table -->
        <div class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead>
              <tr class="text-zinc-700">
                <th class="px-3 py-1.5 text-left section-label">Model</th>
                <th v-if="hasMultiCtx(h)" class="px-3 py-1.5 text-right section-label">Context</th>
                <th class="px-3 py-1.5 text-right section-label">Tok/s</th>
                <th v-if="hasMultiRun(h)" class="px-3 py-1.5 text-right section-label">Std Dev</th>
                <th class="px-3 py-1.5 text-right section-label">TTFT</th>
                <th class="px-3 py-1.5 text-right section-label">Duration</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, ri) in getSortedResults(h)" :key="ri" style="border-top:1px solid var(--border-subtle)">
                <td class="px-3 py-2 text-zinc-400 font-body">{{ r.provider }} / {{ r.model }}</td>
                <td v-if="hasMultiCtx(h)" class="px-3 py-2 text-right font-mono text-zinc-600">
                  {{ (r.context_tokens ?? 0) === 0 ? 'Base' : formatCtxShort(r.context_tokens) }}
                </td>
                <td class="px-3 py-2 text-right font-mono" :class="ri === 0 ? '' : 'text-zinc-500'" :style="ri === 0 ? 'color:var(--lime)' : ''">
                  {{ r.avg_tokens_per_second }}
                </td>
                <td v-if="hasMultiRun(h)" class="px-3 py-2 text-right font-mono text-zinc-600">
                  {{ r.std_dev_tps > 0 ? '\u00B1' + r.std_dev_tps.toFixed(1) : '-' }}
                </td>
                <td class="px-3 py-2 text-right font-mono text-zinc-600">{{ r.avg_ttft_ms }}ms</td>
                <td class="px-3 py-2 text-right font-mono text-zinc-600">{{ r.avg_total_time_s }}s</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Error state -->
    <div v-if="error" class="text-red-400 text-sm font-body mt-4">{{ error }}</div>

    <!-- Detail Modal -->
    <div
      v-if="detailRun"
      class="fixed inset-0 z-50 flex items-center justify-center"
      style="background:rgba(0,0,0,0.75);"
      @click.self="detailRun = null"
    >
      <div
        class="card rounded-md p-6 mx-4 w-full"
        style="max-width:680px;max-height:85vh;overflow-y:auto;"
      >
        <!-- Modal header -->
        <div class="flex items-center justify-between mb-5">
          <div>
            <span class="section-label">Run Detail</span>
            <p class="text-[11px] font-mono text-zinc-600 mt-0.5">{{ formatTimestamp(detailRun.timestamp) }}</p>
          </div>
          <div class="flex items-center gap-2">
            <button
              @click="rerunBenchmark(detailRun)"
              class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-colors"
              style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
              title="Re-run this benchmark"
            >
              <span class="flex items-center gap-1.5">
                <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                </svg>
                Re-Run
              </span>
            </button>
            <button
              @click="detailRun = null"
              class="text-zinc-500 hover:text-zinc-300 transition-colors"
              style="background:none;border:none;cursor:pointer;"
            >Close</button>
          </div>
        </div>

        <!-- Run config summary -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          <div class="p-3 rounded-sm" style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle)">
            <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Models</p>
            <p class="text-sm font-mono text-zinc-300">{{ getSortedResults(detailRun).length }}</p>
          </div>
          <div class="p-3 rounded-sm" style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle)">
            <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Runs</p>
            <p class="text-sm font-mono text-zinc-300">{{ detailRun.runs || 1 }}</p>
          </div>
          <div class="p-3 rounded-sm" style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle)">
            <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Max Tokens</p>
            <p class="text-sm font-mono text-zinc-300">{{ detailRun.max_tokens || '-' }}</p>
          </div>
          <div class="p-3 rounded-sm" style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle)">
            <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Temperature</p>
            <p class="text-sm font-mono text-zinc-300">{{ detailRun.temperature ?? '-' }}</p>
          </div>
        </div>

        <!-- Context tiers -->
        <div v-if="detailRun.context_tiers && detailRun.context_tiers.length > 0" class="mb-4">
          <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-2">Context Tiers</p>
          <div class="flex gap-2 flex-wrap">
            <span
              v-for="tier in detailRun.context_tiers"
              :key="tier"
              class="text-[10px] font-mono px-2 py-0.5 rounded-sm"
              style="background:rgba(255,255,255,0.04);border:1px solid var(--border-subtle);color:#A1A1AA;"
            >{{ tier === 0 ? 'Base' : formatTier(tier) }}</span>
          </div>
        </div>

        <!-- Prompt preview -->
        <div v-if="detailRun.prompt" class="mb-5">
          <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-2">Prompt</p>
          <div
            class="text-xs font-mono text-zinc-400 px-3 py-2 rounded-sm"
            style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);white-space:pre-wrap;max-height:80px;overflow-y:auto;"
          >{{ detailRun.prompt }}</div>
        </div>

        <!-- Results table -->
        <div>
          <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-2">Results</p>
          <div class="overflow-x-auto rounded-sm" style="border:1px solid var(--border-subtle)">
            <table class="w-full text-xs">
              <thead>
                <tr style="border-bottom:1px solid var(--border-subtle)">
                  <th class="px-3 py-2 text-left section-label">Model</th>
                  <th v-if="hasMultiCtx(detailRun)" class="px-3 py-2 text-right section-label">Context</th>
                  <th class="px-3 py-2 text-right section-label">Tok/s</th>
                  <th class="px-3 py-2 text-right section-label">TTFT</th>
                  <th class="px-3 py-2 text-right section-label">Duration</th>
                  <th class="px-3 py-2 text-right section-label">Status</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(r, ri) in getSortedResults(detailRun)"
                  :key="ri"
                  style="border-top:1px solid var(--border-subtle)"
                >
                  <td class="px-3 py-2 text-zinc-400 font-body">{{ r.provider }} / {{ r.model }}</td>
                  <td v-if="hasMultiCtx(detailRun)" class="px-3 py-2 text-right font-mono text-zinc-600">
                    {{ (r.context_tokens ?? 0) === 0 ? 'Base' : formatCtxShort(r.context_tokens) }}
                  </td>
                  <td class="px-3 py-2 text-right font-mono" :style="ri === 0 ? 'color:var(--lime)' : 'color:#71717A'">
                    {{ r.avg_tokens_per_second }}
                  </td>
                  <td class="px-3 py-2 text-right font-mono text-zinc-600">{{ r.avg_ttft_ms }}ms</td>
                  <td class="px-3 py-2 text-right font-mono text-zinc-600">{{ r.avg_total_time_s }}s</td>
                  <td class="px-3 py-2 text-right">
                    <span
                      v-if="r.success !== false"
                      class="text-[10px] font-mono px-1.5 py-0.5 rounded-sm"
                      style="background:rgba(191,255,0,0.08);color:var(--lime);"
                    >ok</span>
                    <span
                      v-else
                      class="text-[10px] font-mono px-1.5 py-0.5 rounded-sm"
                      style="background:rgba(239,68,68,0.08);color:#EF4444;"
                      :title="r.error || ''"
                    >fail</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { apiFetch } from '../utils/api.js'
import { useBenchmarkStore } from '../stores/benchmark.js'
import { useToast } from '../composables/useToast.js'
import { useModal } from '../composables/useModal.js'
import { getColor } from '../utils/constants.js'

const router = useRouter()
const benchmarkStore = useBenchmarkStore()
const { showToast } = useToast()
const { confirm } = useModal()

const history = ref([])
const loading = ref(true)
const error = ref('')
const searchQuery = ref('')
const selectedRuns = ref(new Set())
const compareResult = ref(null)
const detailRun = ref(null)

const filteredHistory = computed(() => {
  if (!searchQuery.value) return history.value
  const q = searchQuery.value.toLowerCase()
  return history.value.filter(h => {
    const ts = new Date(h.timestamp).toLocaleString().toLowerCase()
    const results = (h.results || [])
    const models = results.map(r => `${r.provider} ${r.model}`).join(' ').toLowerCase()
    return ts.includes(q) || models.includes(q)
  })
})

async function loadHistory() {
  loading.value = true
  error.value = ''
  try {
    const res = await apiFetch('/api/history')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    history.value = data.runs || []
  } catch {
    error.value = 'Failed to load history.'
  } finally {
    loading.value = false
  }
}

async function refreshHistory() {
  await loadHistory()
  showToast('History refreshed', 'success')
}

function getSortedResults(h) {
  return [...(h.results || [])].map(r => ({
    ...r,
    avg_tokens_per_second: r.avg_tokens_per_second ?? r.tokens_per_second ?? 0,
    avg_ttft_ms: r.avg_ttft_ms ?? r.ttft_ms ?? 0,
    avg_total_time_s: r.avg_total_time_s ?? r.total_time_s ?? 0,
    std_dev_tps: r.std_dev_tps ?? 0,
  })).sort((a, b) => b.avg_tokens_per_second - a.avg_tokens_per_second)
}

function getWinner(h) {
  const sorted = getSortedResults(h)
  return sorted[0] || null
}

function hasMultiRun(h) {
  return getSortedResults(h).some(r => (r.runs || 1) > 1)
}

function hasMultiCtx(h) {
  const ctxSet = new Set(getSortedResults(h).map(r => r.context_tokens ?? 0))
  return ctxSet.size > 1
}

function formatTimestamp(ts) {
  return new Date(ts).toLocaleString()
}

function formatTier(t) {
  if (t === 0) return '0'
  if (t >= 1000) return (t / 1000) + 'K'
  return String(t)
}

function formatCtxShort(tokens) {
  if (tokens >= 1000) return (tokens / 1000) + 'K'
  return String(tokens)
}

function toggleRunSelection(id) {
  const s = new Set(selectedRuns.value)
  if (s.has(id)) {
    s.delete(id)
  } else {
    s.add(id)
  }
  selectedRuns.value = s
}

function openDetail(h) {
  detailRun.value = h
}

async function rerunBenchmark(h) {
  // Load full run details if needed (detail endpoint has config_json)
  let runData = h
  if (h.id && !h.config) {
    try {
      const res = await apiFetch(`/api/history/${h.id}`)
      if (res.ok) runData = await res.json()
    } catch {
      // use what we have
    }
  }
  benchmarkStore.prefillFromRun(runData)
  detailRun.value = null
  showToast('Settings pre-filled from run. Adjust and submit.', 'success')
  router.push({ name: 'Benchmark' })
}

async function deleteRun(runId) {
  const ok = await confirm('Delete Run', 'Delete this benchmark run?', { danger: true, confirmLabel: 'Delete' })
  if (!ok) return
  try {
    const res = await apiFetch(`/api/history/${runId}`, { method: 'DELETE' })
    if (res.ok) {
      showToast('Run deleted', 'success')
      history.value = history.value.filter(r => r.id !== runId)
      if (detailRun.value?.id === runId) detailRun.value = null
    } else {
      const err = await res.json().catch(() => ({}))
      showToast(err.error || 'Failed to delete run', 'error')
    }
  } catch {
    showToast('Failed to delete run', 'error')
  }
}

async function compareRuns() {
  const runIds = Array.from(selectedRuns.value)
  if (runIds.length < 2) {
    showToast('Select at least 2 runs to compare', 'error')
    return
  }

  try {
    const runs = await Promise.all(runIds.map(async id => {
      const res = await apiFetch(`/api/history/${id}`)
      return res.json()
    }))

    // Collect all unique models
    const allModels = new Set()
    runs.forEach(run => {
      (run.results || []).forEach(r => {
        allModels.add(`${r.provider}||${r.model}`)
      })
    })
    const modelList = Array.from(allModels).sort()

    // Build labels
    const labels = runs.map(r => {
      const d = new Date(r.timestamp)
      return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    })

    // Build rows
    const rows = modelList.map(modelKey => {
      const [provider, model] = modelKey.split('||')
      const color = getColor(provider)
      const values = runs.map(run => {
        const match = (run.results || []).find(r => r.provider === provider && r.model === model)
        if (!match) return null
        return match.avg_tokens_per_second ?? match.tokens_per_second ?? 0
      })
      return { key: modelKey, provider, model, color, values }
    })

    compareResult.value = { labels, rows }
  } catch {
    showToast('Failed to load comparison data', 'error')
  }
}

function getCellColor(value, allValues) {
  if (value === null) return 'text-zinc-700'
  const valid = allValues.filter(v => v !== null && v > 0)
  if (valid.length < 2) return 'text-zinc-400'
  const max = Math.max(...valid)
  const min = Math.min(...valid)
  if (max === min) return 'text-zinc-400'
  if (value === max) return 'text-green-400'
  if (value === min) return 'text-red-400'
  return 'text-zinc-400'
}

onMounted(loadHistory)
</script>

<style scoped>
.lime-btn {
  font-size: 11px;
  font-family: 'Chakra Petch', sans-serif;
  font-weight: 500;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  padding: 6px 16px;
  border-radius: 2px;
  color: var(--lime);
  border: 1px solid rgba(191,255,0,0.2);
  background: transparent;
  cursor: pointer;
  transition: border-color 0.15s;
}
.lime-btn:hover {
  border-color: rgba(191,255,0,0.5);
}
</style>
