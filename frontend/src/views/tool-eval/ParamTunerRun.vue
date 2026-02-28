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
          <!-- 2A: Strategy badge -->
          <span v-if="store.optimizationMode && store.optimizationMode !== 'grid'"
            class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm"
            :style="strategyBadgeStyle(store.optimizationMode)"
          >{{ store.optimizationMode }}</span>
        </div>
        <div class="flex items-center gap-3">
          <!-- 2A: Early stopping indicator for Bayesian -->
          <span v-if="store.earlyStopped"
            class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm"
            style="background:rgba(251,191,36,0.08);color:#FBBF24;border:1px solid rgba(251,191,36,0.2);"
            title="Bayesian search converged early"
          >Converged</span>
          <span v-if="store.progress.eta" class="text-[10px] font-mono text-zinc-600">{{ store.progress.eta }}</span>
          <span class="text-xs font-mono text-zinc-600">{{ store.progress.pct }}%</span>
        </div>
      </div>
      <div class="progress-track rounded-full overflow-hidden">
        <div class="progress-fill" :style="{ width: store.progress.pct + '%' }"></div>
      </div>
      <!-- 2A: Iteration label for Bayesian/Random -->
      <div v-if="store.optimizationMode && store.optimizationMode !== 'grid' && store.progress.iteration"
        class="text-[10px] text-zinc-600 font-mono mt-1"
      >Trial {{ store.progress.iteration }}/{{ store.progress.totalIterations || '?' }}</div>
    </div>

    <!-- 2A: Convergence chart for Bayesian runs -->
    <div v-if="store.optimizationMode === 'bayesian' && convergenceData.length > 1" class="card rounded-md p-5 mb-6">
      <div class="flex items-center justify-between mb-3">
        <span class="section-label">Convergence</span>
        <span class="text-[10px] text-zinc-600 font-body">Score vs iteration</span>
      </div>
      <div class="relative" style="height:80px;">
        <svg class="w-full h-full" :viewBox="`0 0 ${convergenceData.length * 8} 100`" preserveAspectRatio="none">
          <!-- Best score line -->
          <polyline
            :points="bestScoreLine"
            fill="none"
            stroke="var(--lime)"
            stroke-width="1.5"
          />
          <!-- Individual trial dots -->
          <circle
            v-for="(d, i) in convergenceData"
            :key="i"
            :cx="i * 8 + 4"
            :cy="100 - d.score * 100"
            r="1.5"
            :fill="d.isBest ? 'var(--lime)' : 'rgba(191,255,0,0.3)'"
          />
        </svg>
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

      <!-- Action buttons (visible when run is complete) -->
      <div v-if="!store.isRunning" class="flex items-center gap-2 mt-3 pt-3" style="border-top:1px solid var(--border-subtle);">
        <button
          @click="applyBestConfig"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-colors"
          style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);cursor:pointer;"
        >Apply to Context</button>
        <button
          @click="saveAsProfile"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-colors"
          style="background:rgba(255,255,255,0.03);border:1px solid var(--border-subtle);color:var(--zinc-400);cursor:pointer;"
        >Save as Profile</button>
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
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useParamTunerStore } from '../../stores/paramTuner.js'
import { useProfilesStore } from '../../stores/profiles.js'
import { useNotificationsStore } from '../../stores/notifications.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'
import ParamTunerResults from '../../components/tool-eval/ParamTunerResults.vue'

const store = useParamTunerStore()
const profilesStore = useProfilesStore()
const notifStore = useNotificationsStore()
const { setConfig } = useSharedContext()
const { showToast } = useToast()
const { inputModal } = useModal()

const selectedResult = ref(null)

// 2A: Convergence data for Bayesian chart
const convergenceData = computed(() => {
  if (!store.results.length) return []
  let best = 0
  return store.results.map((r, i) => {
    const score = r.overall_score || 0
    const isBest = score > best
    if (isBest) best = score
    return { index: i, score, bestSoFar: best, isBest }
  })
})

const bestScoreLine = computed(() => {
  if (!convergenceData.value.length) return ''
  return convergenceData.value
    .map((d, i) => `${i * 8 + 4},${100 - d.bestSoFar * 100}`)
    .join(' ')
})

function strategyBadgeStyle(mode) {
  if (mode === 'bayesian') return { background: 'rgba(168,85,247,0.08)', color: '#A855F7', border: '1px solid rgba(168,85,247,0.2)' }
  if (mode === 'random') return { background: 'rgba(56,189,248,0.08)', color: '#38BDF8', border: '1px solid rgba(56,189,248,0.2)' }
  return {}
}

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
    if (msg.type === 'eval_warning') {
      showToast(msg.detail || 'Warning', '')
    }
    if (msg.type === 'param_adjustments' && msg.models) {
      for (const m of msg.models) {
        const descs = (m.adjustments || []).map(a =>
          a.action === 'drop' ? `${a.param} dropped` :
          a.action === 'clamp' ? `${a.param} clamped ${a.original}→${a.adjusted}` :
          a.action === 'rename' ? `${a.param} renamed` :
          `${a.param} ${a.action}`
        )
        if (descs.length) showToast(`${m.model_id}: ${descs.join(', ')}`, '')
      }
    }
    if (msg.type === 'judge_failed') {
      showToast(msg.detail || 'Judge analysis failed', 'error')
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

function applyBestConfig() {
  const bestConfig = store.bestConfig?.config
  if (!bestConfig) {
    showToast('No best config available', 'error')
    return
  }

  const updates = { lastUpdatedBy: 'param_tuner' }
  if (bestConfig.temperature != null) updates.temperature = bestConfig.temperature
  if (bestConfig.tool_choice) updates.toolChoice = bestConfig.tool_choice
  if (bestConfig.provider_params) updates.providerParams = bestConfig.provider_params

  setConfig(updates)
  showToast('Best config applied to shared context', 'success')
}

async function saveAsProfile() {
  const bestConfig = store.bestConfig?.config
  if (!bestConfig) {
    showToast('No best config available', 'error')
    return
  }

  const modelId = store.bestConfig.model_id || store.bestConfig.model_name || null

  const result = await inputModal('Save as Profile', 'Profile name', { confirmLabel: 'Save' })
  if (!result?.value?.trim()) return

  try {
    await profilesStore.createFromTuner({
      source_type: 'param_tuner',
      source_id: store.activeRunId,
      model_id: modelId,
      name: result.value.trim(),
      params_json: bestConfig,
      system_prompt: null,
    })
    showToast('Profile saved', 'success')
  } catch (e) {
    showToast(e.message || 'Failed to save profile', 'error')
  }
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
