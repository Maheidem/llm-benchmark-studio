<template>
  <!-- Running state: live progress -->
  <div v-if="jgStore.isRunning" class="card rounded-md p-5 mb-6">
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center gap-3">
        <div class="pulse-dot"></div>
        <span class="text-sm text-zinc-400 font-body">{{ jgStore.progress.detail }}</span>
      </div>
      <div class="flex items-center gap-3">
        <span v-if="jgStore.verdicts.length" class="text-xs font-mono text-zinc-600">{{ jgStore.verdicts.length }} verdicts</span>
        <span class="text-xs font-mono text-zinc-600">{{ jgStore.progress.pct }}%</span>
      </div>
    </div>
    <div class="progress-track rounded-full overflow-hidden">
      <div class="progress-fill" :style="{ width: jgStore.progress.pct + '%' }"></div>
    </div>
  </div>

  <!-- Skipped state: auto-judge was skipped -->
  <div
    v-else-if="jgStore.autoJudgeStatus?.type === 'skipped'"
    class="mb-6 px-4 py-3 rounded-sm flex items-center justify-between"
    style="background:rgba(251,191,36,0.06);border:1px solid rgba(251,191,36,0.25);"
  >
    <div class="flex items-center gap-2">
      <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="#FBBF24" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
      </svg>
      <span class="text-xs text-zinc-400 font-body">
        Auto-judge skipped: {{ skippedMessage }}
      </span>
    </div>
  </div>

  <!-- Just completed state: show View Report link -->
  <div
    v-else-if="justCompleted"
    class="mb-6 px-4 py-3 rounded-sm flex items-center justify-between"
    style="background:rgba(191,255,0,0.06);border:1px solid rgba(191,255,0,0.25);"
  >
    <div class="flex items-center gap-2">
      <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="var(--lime)" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
      </svg>
      <span class="text-xs text-zinc-400 font-body">Judge analysis complete</span>
    </div>
    <router-link
      :to="{ name: 'JudgeHistory' }"
      class="text-[10px] font-display tracking-wider uppercase px-3 py-1 rounded-sm"
      style="color:var(--lime);background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);"
    >View Report</router-link>
  </div>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { useJudgeStore } from '../../stores/judge.js'

const jgStore = useJudgeStore()

const emit = defineEmits(['judge-complete'])

// --- Just completed tracking ---
const justCompleted = ref(false)
let wasRunning = false

watch(() => jgStore.isRunning, (running) => {
  if (running) {
    wasRunning = true
    justCompleted.value = false
  } else if (wasRunning) {
    wasRunning = false
    justCompleted.value = true
    emit('judge-complete')
  }
}, { immediate: true })

// --- Skipped auto-dismiss (10s) ---
let dismissTimer = null

watch(() => jgStore.autoJudgeStatus, (status) => {
  if (dismissTimer) { clearTimeout(dismissTimer); dismissTimer = null }
  if (status?.type === 'skipped') {
    dismissTimer = setTimeout(() => {
      jgStore.clearAutoJudgeStatus()
    }, 10000)
  }
})

onUnmounted(() => {
  if (dismissTimer) clearTimeout(dismissTimer)
})

// --- Computed ---
const skippedMessage = computed(() => {
  const s = jgStore.autoJudgeStatus
  if (!s) return ''
  if (s.reason === 'no_judge_model') return 'no judge model configured'
  if (s.reason === 'score_above_threshold') return s.detail || 'scores above threshold'
  if (s.reason === 'submission_failed') return s.detail || 'failed to start'
  return s.detail || s.reason || 'unknown reason'
})

// Reset justCompleted when new eval starts (clearing stale state)
function clearCompleted() {
  justCompleted.value = false
}

defineExpose({ clearCompleted })
</script>
