<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="section-label">Eval History</h2>
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
              class="fade-in cursor-pointer hover:bg-white/[0.02] transition-colors"
              style="border-top:1px solid var(--border-subtle)"
              :style="`animation-delay: ${idx * 30}ms`"
              @click="openDetail(run)"
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
              <td class="px-4 py-2.5 text-right" @click.stop>
                <div class="flex items-center justify-end gap-2">
                  <!-- Re-run button -->
                  <button
                    @click.stop="rerunEval(run)"
                    class="text-zinc-700 hover:text-lime-400 transition-colors"
                    title="Re-run this eval"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                    </svg>
                  </button>
                  <button
                    @click.stop="deleteRun(run)"
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

    <!-- Detail Modal -->
    <div
      v-if="detailRun"
      class="fixed inset-0 z-50 flex items-center justify-center"
      style="background:rgba(0,0,0,0.75);"
      @click.self="detailRun = null"
    >
      <div
        class="card rounded-md p-6 mx-4 w-full"
        style="max-width:800px;max-height:88vh;overflow-y:auto;"
      >
        <!-- Modal header -->
        <div class="flex items-center justify-between mb-5">
          <div>
            <span class="section-label">Eval Run Detail</span>
            <p class="text-[11px] font-mono text-zinc-600 mt-0.5">{{ formatTimestamp(detailRun.timestamp) }}</p>
          </div>
          <div class="flex items-center gap-2">
            <button
              @click="rerunEval(detailRun)"
              class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-colors flex items-center gap-1.5"
              style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
            >
              <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
              </svg>
              Re-Run
            </button>
            <button
              @click="detailRun = null"
              class="text-zinc-500 hover:text-zinc-300 transition-colors"
              style="background:none;border:none;cursor:pointer;"
            >Close</button>
          </div>
        </div>

        <!-- Loading detail indicator -->
        <div v-if="detailLoading" class="text-xs text-zinc-600 font-body text-center py-6">Loading details...</div>

        <template v-else>
          <!-- Run summary cards -->
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
            <div class="p-3 rounded-sm" style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle)">
              <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Suite</p>
              <p class="text-xs font-body text-zinc-300 truncate">{{ detailRun.suite_name || '-' }}</p>
            </div>
            <div class="p-3 rounded-sm" style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle)">
              <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Models</p>
              <p class="text-sm font-mono text-zinc-300">{{ getModels(detailRun).length }}</p>
            </div>
            <div class="p-3 rounded-sm" style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle)">
              <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Overall Score</p>
              <p class="text-sm font-mono" :style="getScoreStyle(detailRun)">{{ getOverallScore(detailRun) }}</p>
            </div>
            <div class="p-3 rounded-sm" style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle)">
              <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Judge Grade</p>
              <span v-if="detailRun.judge_grade" class="badge text-sm" :style="getGradeStyle(detailRun.judge_grade)">
                {{ detailRun.judge_grade }}
              </span>
              <p v-else class="text-sm font-mono text-zinc-600">-</p>
            </div>
          </div>

          <!-- Per-model summary from summary object -->
          <div v-if="detailRun.summary && Object.keys(detailRun.summary).length > 0" class="mb-5">
            <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-2">Model Scores</p>
            <div class="overflow-x-auto rounded-sm" style="border:1px solid var(--border-subtle)">
              <table class="w-full text-xs">
                <thead>
                  <tr style="border-bottom:1px solid var(--border-subtle)">
                    <th class="px-3 py-2 text-left section-label">Model</th>
                    <th class="px-3 py-2 text-right section-label">Tool Select</th>
                    <th class="px-3 py-2 text-right section-label">Param Acc.</th>
                    <th class="px-3 py-2 text-right section-label">Overall</th>
                    <th class="px-3 py-2 text-right section-label">Cases</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(modelSummary, modelKey) in detailRun.summary"
                    :key="modelKey"
                    style="border-top:1px solid var(--border-subtle)"
                  >
                    <td class="px-3 py-2 font-mono text-zinc-300 text-[11px]">{{ modelKey.split('/').pop() }}</td>
                    <td class="px-3 py-2 text-right font-mono" :style="scoreStyle(modelSummary.tool_selection_score)">
                      {{ formatPct(modelSummary.tool_selection_score) }}
                    </td>
                    <td class="px-3 py-2 text-right font-mono" :style="scoreStyle(modelSummary.param_accuracy_score)">
                      {{ formatPct(modelSummary.param_accuracy_score) }}
                    </td>
                    <td class="px-3 py-2 text-right font-mono font-bold" :style="scoreStyle(modelSummary.overall_score ?? modelSummary.score)">
                      {{ formatPct(modelSummary.overall_score ?? modelSummary.score) }}
                    </td>
                    <td class="px-3 py-2 text-right font-mono text-zinc-600">
                      {{ modelSummary.total_cases ?? modelSummary.cases ?? '-' }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- Per-case results (from full detail fetch) -->
          <div v-if="detailRun.results && detailRun.results.length > 0">
            <div class="flex items-center justify-between mb-2">
              <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600">Test Cases</p>
              <span class="text-[10px] font-mono text-zinc-600">{{ detailRun.results.length }} results</span>
            </div>
            <div class="overflow-x-auto rounded-sm" style="border:1px solid var(--border-subtle);max-height:320px;overflow-y:auto;">
              <table class="w-full text-xs">
                <thead class="sticky top-0" style="background:var(--surface-raised, #1a1a1a)">
                  <tr style="border-bottom:1px solid var(--border-subtle)">
                    <th class="px-3 py-2 text-left section-label">Model</th>
                    <th class="px-3 py-2 text-left section-label">Prompt</th>
                    <th class="px-3 py-2 text-left section-label">Expected</th>
                    <th class="px-3 py-2 text-left section-label">Actual</th>
                    <th class="px-3 py-2 text-right section-label">Score</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(r, ri) in detailRun.results"
                    :key="ri"
                    style="border-top:1px solid var(--border-subtle)"
                  >
                    <td class="px-3 py-2 font-mono text-zinc-400 text-[10px] whitespace-nowrap">
                      {{ (r.model_name || r.model_id || '').split('/').pop() }}
                    </td>
                    <td class="px-3 py-2 text-zinc-500 font-body" style="max-width:180px;">
                      {{ truncate(r.prompt || r.test_case_id || '', 50) }}
                      <span v-if="r.error" class="text-[9px] ml-1 px-1 py-0.5 rounded-sm" style="color:var(--coral);background:rgba(255,59,92,0.08);border:1px solid rgba(255,59,92,0.15);" :title="r.error">ERR</span>
                    </td>
                    <td class="px-3 py-2 font-mono text-zinc-600 text-[10px]">
                      {{ formatTool(r.expected_tool) }}
                    </td>
                    <td class="px-3 py-2 font-mono text-[10px]" :style="r.tool_selection_score > 0 ? 'color:var(--lime)' : 'color:var(--coral, #ff3b5c)'">
                      {{ r.actual_tool || '(none)' }}
                    </td>
                    <td class="px-3 py-2 text-right font-mono font-bold text-[11px]" :style="scoreStyle(r.overall_score)">
                      {{ r.overall_score != null ? (r.overall_score * 100).toFixed(0) + '%' : '-' }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useToolEvalStore } from '../../stores/toolEval.js'
import { useNotificationsStore } from '../../stores/notifications.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'
import { useSharedContext } from '../../composables/useSharedContext.js'

const router = useRouter()
const store = useToolEvalStore()
const notifStore = useNotificationsStore()
const { showToast } = useToast()
const { confirm } = useModal()
const { setSuite, setModels, setConfig } = useSharedContext()

const loading = ref(true)
const error = ref('')
const searchQuery = ref('')
const detailRun = ref(null)
const detailLoading = ref(false)

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

function formatPct(val) {
  if (val == null) return '-'
  return (val * 100).toFixed(1) + '%'
}

function scoreStyle(val) {
  if (val == null) return 'color:#71717A'
  if (val >= 0.8) return 'color:var(--lime)'
  if (val >= 0.5) return 'color:#FBBF24'
  return 'color:#EF4444'
}

function truncate(s, max) {
  if (!s) return ''
  return s.length > max ? s.slice(0, max) + '...' : s
}

function formatTool(tool) {
  if (tool === null || tool === undefined) return '(none)'
  if (Array.isArray(tool)) return tool.join(' | ')
  if (typeof tool === 'object') return JSON.stringify(tool)
  return tool
}

function exportCsv() {
  window.open('/api/export/tool-eval?format=csv')
}

async function openDetail(run) {
  // Start with the list-level data immediately, then fetch full detail in bg
  detailRun.value = { ...run }
  detailLoading.value = true
  try {
    const full = await store.loadHistoryRun(run.id)
    detailRun.value = full
  } catch {
    // keep the partial data already shown
  } finally {
    detailLoading.value = false
  }
}

async function rerunEval(run) {
  // Load full run config if not already available
  let runData = run
  if (run.id && !run.config && !run.results) {
    try {
      runData = await store.loadHistoryRun(run.id)
    } catch {
      // use what we have
    }
  }

  // Pre-fill shared context from this run's config
  const config = runData.config || {}

  // Set suite
  if (runData.suite_id || config.suite_id) {
    const suiteId = runData.suite_id || config.suite_id
    const suiteName = runData.suite_name || config.suite_name || suiteId
    setSuite(suiteId, suiteName)
  }

  // Set models — prefer models array (list of compound keys or model_ids)
  const models = getModels(runData)
  if (models.length > 0) {
    setModels(models)
  }

  // Set eval config
  setConfig({
    temperature: config.temperature ?? runData.temperature ?? 0.0,
    toolChoice: config.tool_choice ?? runData.tool_choice ?? 'required',
    systemPrompts: config.system_prompt ?? runData.system_prompt ?? {},
    lastUpdatedBy: null,
  })

  detailRun.value = null
  showToast('Settings pre-filled from run. Adjust and submit.', 'success')
  router.push({ name: 'ToolEvalEvaluate' })
}

async function refreshHistory() {
  loading.value = true
  try {
    await store.loadHistory()
    showToast('History refreshed', 'success')
  } catch {
    showToast('Failed to refresh history', 'error')
  } finally {
    loading.value = false
  }
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
    if (detailRun.value?.id === run.id) detailRun.value = null
  } catch {
    showToast('Failed to delete eval run', 'error')
  }
}

// Auto-refresh history when judge completes so judge_grade column updates
let unsubscribe = null

onMounted(async () => {
  try {
    await store.loadHistory()
  } catch {
    error.value = 'Failed to load eval history.'
  } finally {
    loading.value = false
  }
  unsubscribe = notifStore.onMessage((msg) => {
    if (msg.type === 'judge_complete') {
      store.loadHistory().catch(() => {})  // silent refresh — grade column updates
    }
  })
})

onUnmounted(() => {
  if (unsubscribe) unsubscribe()
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
