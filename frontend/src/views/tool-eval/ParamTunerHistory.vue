<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Param Tuner History</h2>
        <p class="text-sm text-zinc-600 font-body">Past parameter tuning runs.</p>
      </div>
      <router-link :to="{ name: 'ParamTunerConfig' }"
        class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
        style="border:1px solid var(--border-subtle);color:var(--zinc-400);"
      >New Tune</router-link>
    </div>

    <div v-if="loading" class="text-xs text-zinc-600 font-body text-center py-8">Loading...</div>

    <div v-else-if="store.history.length === 0" class="text-xs text-zinc-600 font-body text-center py-8">
      No param tuning runs yet.
      <router-link :to="{ name: 'ParamTunerConfig' }" class="text-lime-400 hover:text-lime-300">Start your first tune</router-link>
    </div>

    <div v-else class="space-y-3">
      <div v-for="run in store.history" :key="run.id"
        class="card rounded-md px-5 py-4 flex items-center gap-4 cursor-pointer hover:bg-white/[0.02] transition-colors"
        @click="viewRun(run)"
      >
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 mb-1">
            <span class="text-xs font-mono text-zinc-300">{{ run.suite_name || 'Suite' }}</span>
            <span class="text-[10px] font-mono px-1.5 py-0.5 rounded-sm"
              :style="statusStyle(run.status)"
            >{{ run.status || 'unknown' }}</span>
            <!-- 2A: Strategy badge -->
            <span v-if="run.optimization_mode && run.optimization_mode !== 'grid'"
              class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm"
              :style="run.optimization_mode === 'bayesian'
                ? { background: 'rgba(168,85,247,0.08)', color: '#A855F7', border: '1px solid rgba(168,85,247,0.2)' }
                : { background: 'rgba(56,189,248,0.08)', color: '#38BDF8', border: '1px solid rgba(56,189,248,0.2)' }"
            >{{ run.optimization_mode }}</span>
          </div>
          <div class="text-[10px] text-zinc-600 font-body">
            {{ formatDate(run.timestamp) }}
            <span v-if="run.total_combos" class="ml-2">{{ run.completed_combos || 0 }}/{{ run.total_combos }} combos</span>
            <span v-if="run.duration_s" class="ml-2">{{ formatDuration(run.duration_s) }}</span>
          </div>
          <div v-if="run.models_json" class="text-[10px] text-zinc-600 font-body mt-1">
            Models: {{ formatModels(run.models_json) }}
          </div>
        </div>

        <!-- Best score -->
        <div class="text-right">
          <div v-if="run.best_score" class="text-sm font-mono font-bold" :style="{ color: scoreColor(run.best_score * 100) }">
            {{ (run.best_score * 100).toFixed(1) }}%
          </div>
          <div class="text-[10px] text-zinc-600 font-body">best</div>
        </div>

        <!-- Actions -->
        <div class="flex items-center gap-2">
          <button
            v-if="run.best_score"
            @click.stop="applyBest(run)"
            class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm"
            style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
            title="Apply best config to shared context"
          >Apply</button>
          <button
            v-if="run.best_score"
            @click.stop="saveAsProfile(run)"
            class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm"
            style="background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.2);color:#38BDF8;"
            title="Save best config as a profile"
          >Save Profile</button>
          <button
            v-if="run.eval_run_id"
            @click.stop="runJudge(run)"
            class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm"
            style="background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.2);color:#FBBF24;"
            title="Run judge analysis on winning parameters"
          >Judge</button>
          <button
            @click.stop="deleteRun(run)"
            class="text-[10px] text-zinc-600 hover:text-red-400 transition-colors"
            style="background:none;border:none;cursor:pointer;"
            title="Delete run"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
          </button>
        </div>
      </div>
    </div>

    <!-- Detail View -->
    <div v-if="selectedRun" class="fixed inset-0 z-50 flex items-center justify-center" style="background:rgba(0,0,0,0.7);" @click.self="selectedRun = null">
      <div class="card rounded-md p-6 max-w-4xl w-full mx-4" style="max-height:85vh;overflow-y:auto;">
        <div class="flex items-center justify-between mb-4">
          <div>
            <span class="section-label">{{ selectedRun.suite_name || 'Run Detail' }}</span>
            <span class="text-xs text-zinc-600 font-body ml-2">{{ formatDate(selectedRun.timestamp) }}</span>
          </div>
          <button @click="selectedRun = null" class="text-zinc-500 hover:text-zinc-300" style="background:none;border:none;cursor:pointer;">Close</button>
        </div>

        <ParamTunerResults
          v-if="store.results.length > 0"
          :results="store.sortedResults"
          :best-overall-score="store.bestScore"
          @sort="store.setSort($event)"
        />

        <!-- 2B: Score with Judge button & correlation view -->
        <div v-if="store.results.length > 0" class="mt-4">
          <div class="flex items-center gap-2 mb-3">
            <button
              v-if="selectedRun?.eval_run_id && !correlationData.length"
              @click="scoreWithJudge"
              :disabled="judgeScoringLoading"
              class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm inline-flex items-center gap-1.5"
              style="background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.2);color:#FBBF24;"
            >
              <span v-if="judgeScoringLoading" class="inline-block w-2.5 h-2.5 border border-yellow-400/50 border-t-yellow-400 rounded-full animate-spin"></span>
              {{ judgeScoringLoading ? 'Scoring...' : 'Score with Judge' }}
            </button>
            <span v-if="correlationData.length" class="text-[10px] text-zinc-600 font-body">
              Judge scores loaded — showing 3-axis correlation
            </span>
          </div>

          <CorrelationView
            v-if="correlationData.length"
            :data="correlationData"
            @select="selectedResult = $event"
          />
        </div>
      </div>
    </div>

    <!-- Combo detail mini-modal from correlation click -->
    <div v-if="selectedResult" class="fixed inset-0 z-[60] flex items-center justify-center" style="background:rgba(0,0,0,0.7);" @click.self="selectedResult = null">
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
        <div class="flex flex-wrap gap-2">
          <span v-for="(val, key) in selectedResult.config" :key="key"
            class="text-[10px] font-mono px-2 py-1 rounded-sm"
            style="background:rgba(255,255,255,0.04);border:1px solid var(--border-subtle);"
          >{{ key }}: {{ formatValue(val) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useParamTunerStore } from '../../stores/paramTuner.js'
import { useProfilesStore } from '../../stores/profiles.js'
import { useJudgeStore } from '../../stores/judge.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'
import { apiFetch } from '../../utils/api.js'
import ParamTunerResults from '../../components/tool-eval/ParamTunerResults.vue'
import CorrelationView from '../../components/tool-eval/CorrelationView.vue'

const store = useParamTunerStore()
const profilesStore = useProfilesStore()
const judgeStore = useJudgeStore()
const { setConfig } = useSharedContext()
const { showToast } = useToast()
const { inputModal } = useModal()

const loading = ref(true)
const selectedRun = ref(null)
const selectedResult = ref(null)

// 2B: correlation state
const correlationData = ref([])
const judgeScoringLoading = ref(false)

async function scoreWithJudge() {
  if (!selectedRun.value?.eval_run_id || judgeScoringLoading.value) return
  judgeScoringLoading.value = true
  try {
    const res = await apiFetch(`/api/param-tune/correlation/${selectedRun.value.id}/score`, { method: 'POST' })
    if (!res.ok) throw new Error('Failed to score')
    const data = await res.json()
    buildCorrelationData(data)
    showToast('Judge scores loaded', 'success')
  } catch (e) {
    showToast(e.message || 'Failed to score with judge', 'error')
  } finally {
    judgeScoringLoading.value = false
  }
}

function buildCorrelationData(scoreData) {
  // scoreData: { results: [{ combo_id, model_id, judge_score, throughput, cost, ... }] }
  const scored = scoreData.results || scoreData || []
  const map = {}
  for (const s of scored) {
    map[s.combo_id || s.model_id] = s
  }

  correlationData.value = store.results.map(r => {
    const key = r.combo_id || r.model_id
    const s = map[key] || {}
    return {
      result: r,
      model_name: r.model_name || r.model_id || '',
      config_label: Object.entries(r.config || {}).map(([k, v]) => `${k}=${v}`).join(' '),
      throughput: r.throughput_tps || s.throughput || null,
      quality: s.judge_score != null ? s.judge_score : (r.overall_score || 0),
      cost: r.cost_usd || s.cost || null,
    }
  })
}

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
}

function formatValue(val) {
  if (val === undefined || val === null) return '-'
  if (typeof val === 'number') return Number.isInteger(val) ? val.toString() : val.toFixed(3)
  return String(val)
}

onMounted(async () => {
  try {
    await store.loadHistory()
  } catch {
    showToast('Failed to load history', 'error')
  } finally {
    loading.value = false
  }
})

async function viewRun(run) {
  try {
    await store.loadRun(run.id)
    selectedRun.value = run
    correlationData.value = []  // reset on new run view
  } catch {
    showToast('Failed to load run details', 'error')
  }
}

async function deleteRun(run) {
  if (!confirm(`Delete this tuning run?`)) return
  try {
    await store.deleteRun(run.id)
    showToast('Run deleted', 'success')
    if (selectedRun.value?.id === run.id) selectedRun.value = null
  } catch {
    showToast('Failed to delete run', 'error')
  }
}

function applyBest(run) {
  // Parse best config and apply to shared context
  let bestConfig = null
  if (run.best_config_json) {
    try {
      bestConfig = typeof run.best_config_json === 'string' ? JSON.parse(run.best_config_json) : run.best_config_json
    } catch { /* ignore */ }
  }

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

async function saveAsProfile(run) {
  let bestConfig = null
  if (run.best_config_json) {
    try {
      bestConfig = typeof run.best_config_json === 'string' ? JSON.parse(run.best_config_json) : run.best_config_json
    } catch { /* ignore */ }
  }
  if (!bestConfig) {
    showToast('No best config available', 'error')
    return
  }

  const models = run.models_json
    ? (typeof run.models_json === 'string' ? JSON.parse(run.models_json) : run.models_json)
    : []
  const modelId = Array.isArray(models) && models.length > 0 ? models[0] : null

  const result = await inputModal('Save as Profile', 'Profile name', { confirmLabel: 'Save' })
  if (!result?.value?.trim()) return

  try {
    await profilesStore.createFromTuner({
      source_type: 'param_tuner',
      source_id: run.id,
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

async function runJudge(run) {
  if (!run.eval_run_id) {
    showToast('No eval run linked to this tuning run', 'error')
    return
  }

  // Get default judge model from settings
  let judgeModel = ''
  try {
    const res = await apiFetch('/api/settings/judge')
    if (res.ok) {
      const s = await res.json()
      judgeModel = s.default_judge_model || ''
    }
  } catch { /* non-fatal */ }

  if (!judgeModel) {
    showToast('No default judge model configured. Set one in Settings > Judge.', 'error')
    return
  }

  try {
    await judgeStore.runJudge({
      eval_run_id: run.eval_run_id,
      judge_model: judgeModel,
      tune_run_id: run.id,
      tune_type: 'param_tuner',
    })
    showToast('Judge analyzing winning parameters...', 'success')
  } catch (e) {
    showToast(e.message || 'Failed to start judge', 'error')
  }
}

function formatDate(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function formatDuration(s) {
  if (s < 60) return `${Math.round(s)}s`
  return `${Math.round(s / 60)}m`
}

function formatModels(json) {
  try {
    const models = typeof json === 'string' ? JSON.parse(json) : json
    if (Array.isArray(models)) return models.map(m => m.split('/').pop()).join(', ')
  } catch { /* ignore */ }
  return ''
}

function statusStyle(status) {
  if (status === 'completed') return { background: 'rgba(191,255,0,0.1)', color: 'var(--lime)' }
  if (status === 'running') return { background: 'rgba(56,189,248,0.1)', color: '#38BDF8' }
  if (status === 'cancelled') return { background: 'rgba(255,255,255,0.04)', color: '#71717A' }
  if (status === 'interrupted') return { background: 'rgba(249,115,22,0.1)', color: '#F97316' }
  return { background: 'rgba(255,59,92,0.1)', color: 'var(--coral)' }
}
</script>
