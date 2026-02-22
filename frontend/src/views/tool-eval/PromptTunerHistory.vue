<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Prompt Tuner History</h2>
        <p class="text-sm text-zinc-600 font-body">Past prompt tuning runs.</p>
      </div>
      <router-link :to="{ name: 'PromptTunerConfig' }"
        class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
        style="border:1px solid var(--border-subtle);color:var(--zinc-400);"
      >New Tune</router-link>
    </div>

    <div v-if="loading" class="text-xs text-zinc-600 font-body text-center py-8">Loading...</div>

    <div v-else-if="store.history.length === 0" class="text-xs text-zinc-600 font-body text-center py-8">
      No prompt tuning runs yet.
      <router-link :to="{ name: 'PromptTunerConfig' }" class="text-lime-400 hover:text-lime-300">Start your first tune</router-link>
    </div>

    <div v-else class="space-y-3">
      <div v-for="run in store.history" :key="run.id"
        class="card rounded-md px-5 py-4 cursor-pointer hover:bg-white/[0.02] transition-colors"
        @click="viewRun(run)"
      >
        <div class="flex items-center gap-4">
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 mb-1">
              <span class="text-xs font-mono text-zinc-300">{{ run.suite_name || 'Suite' }}</span>
              <span class="text-[10px] font-mono px-1.5 py-0.5 rounded-sm"
                :style="statusStyle(run.status)"
              >{{ run.status || 'unknown' }}</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded-sm font-body"
                :style="modeStyle(run.mode)"
              >{{ run.mode || 'quick' }}</span>
            </div>
            <div class="text-[10px] text-zinc-600 font-body">
              {{ formatDate(run.timestamp) }}
              <span v-if="run.meta_model" class="ml-2">Meta: {{ run.meta_model.split('/').pop() }}</span>
              <span v-if="run.duration_s" class="ml-2">{{ formatDuration(run.duration_s) }}</span>
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
              v-if="run.best_prompt"
              @click.stop="applyBest(run)"
              class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm"
              style="background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.2);color:#A855F7;"
              title="Apply best prompt to shared context"
            >Apply</button>
            <button
              v-if="run.best_prompt"
              @click.stop="saveAsProfile(run)"
              class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm"
              style="background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.2);color:#38BDF8;"
              title="Save best prompt as a profile"
            >Save Profile</button>
            <button
              v-if="run.eval_run_id"
              @click.stop="runJudge(run)"
              class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm"
              style="background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.2);color:#FBBF24;"
              title="Run judge analysis on winning prompt"
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

        <!-- Best prompt preview -->
        <div v-if="run.best_prompt" class="mt-2 text-[10px] text-zinc-600 font-body truncate">
          "{{ run.best_prompt.substring(0, 100) }}{{ run.best_prompt.length > 100 ? '...' : '' }}"
        </div>
      </div>
    </div>

    <!-- Detail View -->
    <div v-if="selectedRun" class="fixed inset-0 z-50 flex items-center justify-center" style="background:rgba(0,0,0,0.7);" @click.self="selectedRun = null">
      <div class="card rounded-md p-6 max-w-3xl w-full mx-4" style="max-height:85vh;overflow-y:auto;">
        <div class="flex items-center justify-between mb-4">
          <div>
            <span class="section-label">{{ selectedRun.suite_name || 'Run Detail' }}</span>
            <span class="text-xs text-zinc-600 font-body ml-2">{{ formatDate(selectedRun.timestamp) }}</span>
          </div>
          <button @click="selectedRun = null" class="text-zinc-500 hover:text-zinc-300" style="background:none;border:none;cursor:pointer;">Close</button>
        </div>

        <!-- Best prompt -->
        <div v-if="store.bestPrompt" class="mb-4 rounded-sm px-4 py-3" style="border:1px solid rgba(191,255,0,0.2);background:rgba(191,255,0,0.03);">
          <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1">Best Prompt ({{ (store.bestScore * 100).toFixed(1) }}%)</div>
          <div class="text-xs text-zinc-400 font-body mb-2">{{ store.bestPrompt }}</div>
          <div v-if="formatPromptOrigin(selectedRun)" class="text-[10px] text-zinc-600 font-body italic">
            {{ formatPromptOrigin(selectedRun) }}
          </div>
        </div>

        <!-- Timeline -->
        <PromptTimeline
          v-if="store.generations.length > 0"
          :generations="store.generations"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { usePromptTunerStore } from '../../stores/promptTuner.js'
import { useProfilesStore } from '../../stores/profiles.js'
import { useJudgeStore } from '../../stores/judge.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'
import { apiFetch } from '../../utils/api.js'
import PromptTimeline from '../../components/tool-eval/PromptTimeline.vue'

const store = usePromptTunerStore()
const profilesStore = useProfilesStore()
const judgeStore = useJudgeStore()
const { setSystemPrompt, setConfig } = useSharedContext()
const { showToast } = useToast()
const { inputModal } = useModal()

const loading = ref(true)
const selectedRun = ref(null)

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
  } catch {
    showToast('Failed to load run details', 'error')
  }
}

async function deleteRun(run) {
  if (!confirm('Delete this prompt tuning run?')) return
  try {
    await store.deleteRun(run.id)
    showToast('Run deleted', 'success')
    if (selectedRun.value?.id === run.id) selectedRun.value = null
  } catch {
    showToast('Failed to delete run', 'error')
  }
}

function applyBest(run) {
  if (!run.best_prompt) {
    showToast('No best prompt available', 'error')
    return
  }
  setSystemPrompt('_global', run.best_prompt)
  setConfig({ lastUpdatedBy: 'prompt_tuner' })
  showToast('Best prompt applied to shared context', 'success')
}

async function saveAsProfile(run) {
  if (!run.best_prompt) {
    showToast('No best prompt available', 'error')
    return
  }

  const modelId = run.model_id || run.target?.model_id || null

  const result = await inputModal('Save as Profile', 'Profile name', { confirmLabel: 'Save' })
  if (!result?.value?.trim()) return

  try {
    await profilesStore.createFromTuner({
      source_type: 'prompt_tuner',
      source_id: run.id,
      model_id: modelId,
      name: result.value.trim(),
      system_prompt: run.best_prompt,
      params_json: null,
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
      tune_type: 'prompt_tuner',
    })
    showToast('Judge analyzing winning prompt...', 'success')
  } catch (e) {
    showToast(e.message || 'Failed to start judge', 'error')
  }
}

function formatPromptOrigin(run) {
  const raw = run.best_prompt_origin_json || run.best_prompt_origin
  if (!raw) return null
  try {
    const o = typeof raw === 'string' ? JSON.parse(raw) : raw
    const parts = []
    if (o.generation != null) parts.push(`Generation ${o.generation}`)
    if (o.prompt_index != null) parts.push(`prompt ${o.prompt_index}`)
    if (o.style) parts.push(`style: ${o.style}`)
    return parts.length > 0 ? `Best prompt found in ${parts.join(', ')}` : null
  } catch {
    return null
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

function statusStyle(status) {
  if (status === 'completed') return { background: 'rgba(191,255,0,0.1)', color: 'var(--lime)' }
  if (status === 'running') return { background: 'rgba(56,189,248,0.1)', color: '#38BDF8' }
  if (status === 'cancelled') return { background: 'rgba(255,255,255,0.04)', color: '#71717A' }
  if (status === 'interrupted') return { background: 'rgba(249,115,22,0.1)', color: '#F97316' }
  return { background: 'rgba(255,59,92,0.1)', color: 'var(--coral)' }
}

function modeStyle(mode) {
  if (mode === 'evolutionary') return { background: 'rgba(168,85,247,0.1)', color: '#A855F7' }
  return { background: 'rgba(56,189,248,0.08)', color: '#38BDF8' }
}

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
}
</script>
