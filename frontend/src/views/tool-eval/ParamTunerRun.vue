<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Param Tuner Run</h2>
        <p class="text-sm text-zinc-600 font-body">
          {{ store.isRunning ? 'Tuning in progress...' : 'Run complete' }}
        </p>
      </div>
      <div class="flex items-center gap-2">
        <router-link :to="{ name: 'ParamTunerConfig' }"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
          style="border:1px solid var(--border-subtle);color:var(--zinc-400);"
        >Config</router-link>
        <button
          v-if="store.isRunning"
          @click="cancelTuning"
          class="text-[10px] font-display font-medium tracking-wider uppercase px-3 py-1 rounded-sm transition-colors"
          style="color:var(--coral);border:1px solid rgba(255,59,92,0.3);"
        >Cancel</button>
      </div>
    </div>

    <!-- Progress -->
    <div v-if="store.isRunning || store.progress.pct > 0" class="card rounded-md p-5 mb-6">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-3">
          <div v-if="store.isRunning" class="pulse-dot"></div>
          <span class="text-sm text-zinc-400 font-body">{{ store.progress.detail }}</span>
        </div>
        <div class="flex items-center gap-3">
          <span v-if="store.progress.eta" class="text-[10px] font-mono text-zinc-600">{{ store.progress.eta }}</span>
          <span class="text-xs font-mono text-zinc-600">{{ store.progress.pct }}%</span>
        </div>
      </div>
      <div class="progress-track rounded-full overflow-hidden">
        <div class="progress-fill" :style="{ width: store.progress.pct + '%' }"></div>
      </div>
    </div>

    <!-- Best Config Highlight -->
    <div v-if="store.bestConfig" class="card rounded-md p-5 mb-6" style="border-left:3px solid var(--lime);">
      <div class="flex items-center justify-between mb-2">
        <span class="section-label">Best Config</span>
        <span class="text-sm font-mono font-bold" style="color:var(--lime);">
          {{ (store.bestScore * 100).toFixed(1) }}%
        </span>
      </div>
      <div class="flex flex-wrap gap-2">
        <span v-for="(val, key) in store.bestConfig.config" :key="key"
          class="text-[10px] font-mono px-2 py-1 rounded-sm"
          style="background:rgba(191,255,0,0.06);border:1px solid rgba(191,255,0,0.15);color:var(--lime);"
        >{{ key }}: {{ formatValue(val) }}</span>
      </div>
      <div class="text-[10px] text-zinc-600 font-body mt-2">
        {{ store.bestConfig.model_name || store.bestConfig.model_id || '' }}
        - {{ store.bestConfig.cases_passed || 0 }}/{{ store.bestConfig.cases_total || 0 }} cases passed
      </div>
    </div>

    <!-- Live Results -->
    <ParamTunerResults
      v-if="store.results.length > 0"
      :results="store.sortedResults"
      :best-overall-score="store.bestScore"
      @sort="store.setSort($event)"
      @select="onSelectResult"
    />

    <!-- No results yet -->
    <div v-else-if="store.isRunning" class="text-xs text-zinc-600 font-body text-center py-8">
      Waiting for first combo result...
    </div>
    <div v-else-if="!store.isRunning && store.results.length === 0" class="text-xs text-zinc-600 font-body text-center py-8">
      No active run.
      <router-link :to="{ name: 'ParamTunerConfig' }" class="text-lime-400 hover:text-lime-300">Start a new tune</router-link>
    </div>

    <!-- Detail Modal -->
    <div v-if="selectedResult" class="fixed inset-0 z-50 flex items-center justify-center" style="background:rgba(0,0,0,0.7);" @click.self="selectedResult = null">
      <div class="card rounded-md p-6 max-w-2xl w-full mx-4" style="max-height:80vh;overflow-y:auto;">
        <div class="flex items-center justify-between mb-4">
          <span class="section-label">Combo Detail</span>
          <button @click="selectedResult = null" class="text-zinc-500 hover:text-zinc-300" style="background:none;border:none;cursor:pointer;">Close</button>
        </div>

        <div class="mb-3">
          <span class="text-xs font-mono text-zinc-300">{{ selectedResult.model_name }}</span>
          <span class="text-xs font-mono ml-2" :style="{ color: scoreColor(selectedResult.overall_score * 100) }">
            {{ (selectedResult.overall_score * 100).toFixed(1) }}%
          </span>
        </div>

        <div class="flex flex-wrap gap-2 mb-4">
          <span v-for="(val, key) in selectedResult.config" :key="key"
            class="text-[10px] font-mono px-2 py-1 rounded-sm"
            style="background:rgba(255,255,255,0.04);border:1px solid var(--border-subtle);"
          >{{ key }}: {{ formatValue(val) }}</span>
        </div>

        <!-- Adjustments -->
        <div v-if="selectedResult.adjustments?.length > 0" class="mb-4">
          <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1">Adjustments</div>
          <div class="flex flex-wrap gap-2">
            <span v-for="adj in selectedResult.adjustments" :key="adj.param"
              class="text-[10px] font-mono px-2 py-1 rounded-sm"
              :style="adj.type === 'dropped' ? { background: 'rgba(255,59,92,0.1)', color: 'var(--coral)' } : { background: 'rgba(234,179,8,0.1)', color: '#EAB308' }"
            >{{ adj.param }}: {{ adj.type }}{{ adj.type === 'clamped' ? ` (${adj.original} -> ${adj.clamped})` : '' }}</span>
          </div>
        </div>

        <!-- Case results -->
        <div v-if="selectedResult.case_results?.length > 0">
          <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-2">Per-Case Results</div>
          <table class="w-full text-xs results-table">
            <thead>
              <tr style="border-bottom:1px solid var(--border-subtle);">
                <th class="px-3 py-1.5 text-left section-label">Case</th>
                <th class="px-3 py-1.5 text-left section-label">Expected</th>
                <th class="px-3 py-1.5 text-left section-label">Actual</th>
                <th class="px-3 py-1.5 text-right section-label">Score</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(cr, ci) in selectedResult.case_results" :key="ci">
                <td class="px-3 py-1.5 text-xs font-mono text-zinc-500">{{ cr.test_case_id || ci + 1 }}</td>
                <td class="px-3 py-1.5 text-xs font-mono text-zinc-500">{{ cr.expected_tool }}</td>
                <td class="px-3 py-1.5 text-xs font-mono" :style="{ color: cr.tool_selection_score > 0 ? 'var(--lime)' : 'var(--coral)' }">
                  {{ cr.actual_tool || '(none)' }}
                </td>
                <td class="px-3 py-1.5 text-right text-xs font-mono" :style="{ color: scoreColor((cr.overall_score || 0) * 100) }">
                  {{ ((cr.overall_score || 0) * 100).toFixed(0) }}%
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useParamTunerStore } from '../../stores/paramTuner.js'
import { useNotificationsStore } from '../../stores/notifications.js'
import { useToast } from '../../composables/useToast.js'
import ParamTunerResults from '../../components/tool-eval/ParamTunerResults.vue'

const store = useParamTunerStore()
const notifStore = useNotificationsStore()
const { showToast } = useToast()

const selectedResult = ref(null)

let unsubscribe = null

onMounted(() => {
  store.restoreJob()

  // Subscribe to WS messages for param tune events
  unsubscribe = notifStore.onMessage((msg) => {
    if (!store.activeJobId) return
    if (msg.job_id && msg.job_id !== store.activeJobId) return

    const tuneTypes = ['tune_start', 'combo_result', 'tune_complete', 'job_progress', 'job_completed', 'job_failed', 'job_cancelled']
    if (tuneTypes.includes(msg.type)) {
      store.handleProgress(msg)

      if (msg.type === 'tune_complete') {
        showToast('Param tuning complete!', 'success')
      }
      if (msg.type === 'job_failed') {
        showToast(msg.error || 'Tuning failed', 'error')
      }
    }
  })

  // If we have an active run, try to load partial results
  if (store.activeRunId && store.results.length === 0) {
    store.loadRun(store.activeRunId).catch(() => { /* might not exist yet */ })
  }
})

onUnmounted(() => {
  if (unsubscribe) unsubscribe()
})

async function cancelTuning() {
  try {
    await store.cancelTuning()
    showToast('Cancellation requested', '')
  } catch {
    showToast('Failed to cancel', 'error')
  }
}

function onSelectResult(result) {
  selectedResult.value = result
}

function formatValue(val) {
  if (val === undefined || val === null) return '-'
  if (typeof val === 'number') return Number.isInteger(val) ? val.toString() : val.toFixed(3)
  return String(val)
}

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
}
</script>
