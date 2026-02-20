<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Experiment Timeline</h2>
        <p class="text-sm text-zinc-600 font-body">
          {{ experimentName ? experimentName + ' - ' : '' }}Chronological view of all runs.
        </p>
      </div>
    </div>

    <!-- Experiment Selector (if no experiment set in context) -->
    <div v-if="!experimentId" class="card rounded-md p-5 mb-6">
      <label class="section-label mb-2 block">Select Experiment</label>
      <select
        v-model="selectedExperimentId"
        @change="loadTimeline"
        class="text-sm font-mono px-3 py-2 rounded-sm w-full"
        style="background:var(--surface);border:1px solid var(--border-subtle);color:#E4E4E7;outline:none;"
      >
        <option value="">-- Select an experiment --</option>
        <option v-for="exp in experiments" :key="exp.id" :value="exp.id">
          {{ exp.name }} ({{ exp.suite_name || '' }})
        </option>
      </select>
    </div>

    <!-- Baseline info -->
    <div v-if="timeline && timeline.baseline" class="card rounded-md p-4 mb-6 flex items-center gap-4">
      <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Baseline</div>
      <div class="text-xs font-mono" :style="{ color: scoreColor((timeline.baseline.score || 0) * 100) }">
        {{ ((timeline.baseline.score || 0) * 100).toFixed(1) }}%
      </div>
      <div v-if="timeline.best && timeline.best.score" class="ml-auto flex items-center gap-2">
        <span class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Best</span>
        <span class="text-xs font-mono font-bold" style="color:var(--lime);">
          {{ ((timeline.best.score || 0) * 100).toFixed(1) }}%
        </span>
        <span v-if="timeline.best.source" class="text-[10px] text-zinc-600 font-body">
          via {{ timeline.best.source }}
        </span>
      </div>
    </div>

    <div v-if="loading" class="text-xs text-zinc-600 font-body text-center py-8">Loading...</div>

    <div v-else-if="!activeExpId" class="text-xs text-zinc-600 font-body text-center py-8">
      Select an experiment to view its timeline, or create one from the Evaluate page.
    </div>

    <ExperimentTimeline
      v-else-if="entries.length > 0"
      :entries="entries"
      @navigate="onNavigate"
    />

    <div v-else class="text-xs text-zinc-600 font-body text-center py-8">
      No runs in this experiment yet.
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useToolEvalStore } from '../../stores/toolEval.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import { useToast } from '../../composables/useToast.js'
import { apiFetch } from '../../utils/api.js'
import ExperimentTimeline from '../../components/tool-eval/ExperimentTimeline.vue'

const router = useRouter()
const teStore = useToolEvalStore()
const { context } = useSharedContext()
const { showToast } = useToast()

const loading = ref(false)
const experiments = ref([])
const selectedExperimentId = ref('')
const timeline = ref(null)
const entries = ref([])

const experimentId = computed(() => context.experimentId)
const experimentName = computed(() => context.experimentName || timeline.value?.experiment_name || '')
const activeExpId = computed(() => experimentId.value || selectedExperimentId.value)

onMounted(async () => {
  // Load experiments list
  try {
    await teStore.loadExperiments()
    experiments.value = teStore.experiments
  } catch { /* ignore */ }

  // If experiment in context, load its timeline
  if (experimentId.value) {
    await loadTimeline()
  }
})

async function loadTimeline() {
  const expId = activeExpId.value
  if (!expId) return

  loading.value = true
  try {
    const res = await apiFetch(`/api/experiments/${expId}/timeline`)
    if (!res.ok) throw new Error('Failed to load timeline')
    const data = await res.json()
    timeline.value = data
    entries.value = data.entries || []
  } catch {
    showToast('Failed to load timeline', 'error')
    entries.value = []
  } finally {
    loading.value = false
  }
}

function onNavigate(entry) {
  switch (entry.type) {
    case 'eval':
      router.push({ name: 'ToolEvalEvaluate' })
      break
    case 'param_tune':
      router.push({ name: 'ParamTunerHistory' })
      break
    case 'prompt_tune':
      router.push({ name: 'PromptTunerHistory' })
      break
    case 'judge':
      router.push({ name: 'JudgeHistory' })
      break
  }
}

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
}
</script>
