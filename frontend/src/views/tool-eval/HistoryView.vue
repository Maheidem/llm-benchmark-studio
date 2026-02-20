<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="section-label">Eval History</h2>
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
        <button @click="exportCsv" class="lime-btn text-[10px]">Export CSV</button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="text-zinc-600 text-sm font-body">Loading history...</div>

    <!-- Empty state -->
    <div v-else-if="filteredHistory.length === 0 && store.history.length === 0" class="card rounded-sm p-10 text-center">
      <p class="text-zinc-600 font-body">No eval history yet.</p>
      <p class="text-zinc-700 text-xs mt-1 font-body">Run your first tool evaluation to see results here.</p>
    </div>

    <!-- History table -->
    <div v-else class="card rounded-sm overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full text-xs">
          <thead>
            <tr style="border-bottom:1px solid var(--border-subtle)">
              <th class="px-4 py-3 text-left section-label">Date</th>
              <th class="px-4 py-3 text-left section-label">Suite</th>
              <th class="px-4 py-3 text-left section-label">Models</th>
              <th class="px-4 py-3 text-right section-label">Overall Score</th>
              <th class="px-4 py-3 text-center section-label">Judge</th>
              <th class="px-4 py-3 text-right section-label">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(run, idx) in filteredHistory"
              :key="run.id || idx"
              class="fade-in"
              style="border-top:1px solid var(--border-subtle)"
              :style="`animation-delay: ${idx * 30}ms`"
            >
              <td class="px-4 py-2.5 text-zinc-400 font-mono text-[11px] whitespace-nowrap">
                {{ formatTimestamp(run.timestamp) }}
              </td>
              <td class="px-4 py-2.5 text-zinc-300 font-body">
                {{ run.suite_name || '-' }}
              </td>
              <td class="px-4 py-2.5">
                <div class="flex flex-wrap gap-1">
                  <span
                    v-for="model in getModels(run)"
                    :key="model"
                    class="badge"
                    style="background:rgba(255,255,255,0.04);border:1px solid var(--border-subtle);color:var(--zinc-400);font-size:9px"
                  >{{ model }}</span>
                </div>
              </td>
              <td class="px-4 py-2.5 text-right font-mono" :style="getScoreStyle(run)">
                {{ getOverallScore(run) }}
              </td>
              <td class="px-4 py-2.5 text-center">
                <span v-if="run.judge_grade" class="badge" :style="getGradeStyle(run.judge_grade)">
                  {{ run.judge_grade }}
                </span>
                <span v-else class="text-zinc-700">-</span>
              </td>
              <td class="px-4 py-2.5 text-right">
                <div class="flex items-center justify-end gap-2">
                  <button
                    @click="deleteRun(run)"
                    class="text-zinc-700 hover:text-red-400 transition-colors"
                    title="Delete run"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                    </svg>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Error state -->
    <div v-if="error" class="text-red-400 text-sm font-body mt-4">{{ error }}</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useToolEvalStore } from '../../stores/toolEval.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'

const store = useToolEvalStore()
const { showToast } = useToast()
const { confirm } = useModal()

const loading = ref(true)
const error = ref('')
const searchQuery = ref('')

const filteredHistory = computed(() => {
  const runs = store.history || []
  if (!searchQuery.value) return runs
  const q = searchQuery.value.toLowerCase()
  return runs.filter(run => {
    const ts = formatTimestamp(run.timestamp).toLowerCase()
    const suite = (run.suite_name || '').toLowerCase()
    const models = getModels(run).join(' ').toLowerCase()
    return ts.includes(q) || suite.includes(q) || models.includes(q)
  })
})

function getModels(run) {
  if (Array.isArray(run.models)) return run.models
  return []
}

function getOverallScore(run) {
  if (run.summary && typeof run.summary === 'object') {
    const scores = Object.values(run.summary)
      .map(s => {
        if (typeof s === 'object' && s !== null) return s.score ?? s.overall_score ?? s.tool_score ?? null
        if (typeof s === 'number') return s
        return null
      })
      .filter(s => s !== null && s !== undefined)
    if (scores.length > 0) {
      const avg = scores.reduce((a, b) => a + b, 0) / scores.length
      return (avg * 100).toFixed(1) + '%'
    }
  }
  if (run.judge_score !== null && run.judge_score !== undefined) {
    return (run.judge_score * 100).toFixed(1) + '%'
  }
  return '-'
}

function getScoreStyle(run) {
  const score = getOverallScore(run)
  if (score === '-') return 'color:var(--zinc-600)'
  return 'color:var(--lime)'
}

function getGradeStyle(grade) {
  const colors = {
    A: { bg: 'rgba(34,197,94,0.1)', border: 'rgba(34,197,94,0.3)', text: '#22C55E' },
    B: { bg: 'rgba(191,255,0,0.08)', border: 'rgba(191,255,0,0.2)', text: 'var(--lime)' },
    C: { bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.25)', text: '#F59E0B' },
    D: { bg: 'rgba(239,68,68,0.1)', border: 'rgba(239,68,68,0.25)', text: '#EF4444' },
    F: { bg: 'rgba(239,68,68,0.15)', border: 'rgba(239,68,68,0.3)', text: '#EF4444' },
  }
  const letter = grade ? grade.charAt(0).toUpperCase() : ''
  const c = colors[letter] || { bg: 'rgba(255,255,255,0.04)', border: 'var(--border-subtle)', text: '#A1A1AA' }
  return `background:${c.bg};border:1px solid ${c.border};color:${c.text};font-size:10px`
}

function formatTimestamp(ts) {
  if (!ts) return '-'
  return new Date(ts).toLocaleString()
}

function exportCsv() {
  window.open('/api/export/tool-eval?format=csv')
}

async function deleteRun(run) {
  const ok = await confirm('Delete Eval Run', `Delete eval run from ${formatTimestamp(run.timestamp)}?`, {
    danger: true,
    confirmLabel: 'Delete',
  })
  if (!ok) return
  try {
    await store.deleteHistoryRun(run.id)
    showToast('Eval run deleted', 'success')
  } catch {
    showToast('Failed to delete eval run', 'error')
  }
}

onMounted(async () => {
  try {
    await store.loadHistory()
  } catch {
    error.value = 'Failed to load eval history.'
  } finally {
    loading.value = false
  }
})
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
