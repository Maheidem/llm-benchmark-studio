<template>
  <div class="max-w-7xl mx-auto px-6 py-8">
    <div class="flex items-center justify-between mb-6">
      <h2 class="section-label">Benchmark History</h2>
      <div class="flex items-center gap-3">
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
        class="card rounded-sm p-5 fade-in"
        :style="`animation-delay: ${idx * 40}ms`"
      >
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-3">
            <!-- Compare checkbox -->
            <button
              v-if="h.id"
              @click="toggleRunSelection(h.id)"
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
            <button v-if="h.id" @click="deleteRun(h.id)" class="text-zinc-700 hover:text-red-400 transition-colors" title="Delete run">
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
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { apiFetch } from '../utils/api.js'
import { useToast } from '../composables/useToast.js'
import { useModal } from '../composables/useModal.js'
import { getColor } from '../utils/constants.js'

const { showToast } = useToast()
const { confirm } = useModal()

const history = ref([])
const loading = ref(true)
const error = ref('')
const searchQuery = ref('')
const selectedRuns = ref(new Set())
const compareResult = ref(null)

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
  } catch (e) {
    error.value = 'Failed to load history.'
    console.error('[loadHistory]', e)
  } finally {
    loading.value = false
  }
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

async function deleteRun(runId) {
  const ok = await confirm('Delete Run', 'Delete this benchmark run?', { danger: true, confirmLabel: 'Delete' })
  if (!ok) return
  try {
    const res = await apiFetch(`/api/history/${runId}`, { method: 'DELETE' })
    if (res.ok) {
      showToast('Run deleted', 'success')
      await loadHistory()
    } else {
      const err = await res.json()
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
  } catch (e) {
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
