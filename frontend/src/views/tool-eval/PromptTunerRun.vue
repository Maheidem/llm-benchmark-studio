<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Prompt Tuner Run</h2>
        <p class="text-sm text-zinc-600 font-body">
          {{ store.isRunning ? 'Tuning in progress...' : 'Run complete' }}
        </p>
      </div>
      <div class="flex items-center gap-2">
        <router-link :to="{ name: 'PromptTunerConfig' }"
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

    <!-- Best Prompt Highlight -->
    <div v-if="store.bestPrompt" class="card rounded-md p-5 mb-6" style="border-left:3px solid var(--lime);">
      <div class="flex items-center justify-between mb-2">
        <span class="section-label">Best Prompt</span>
        <span class="text-sm font-mono font-bold" style="color:var(--lime);">
          {{ (store.bestScore * 100).toFixed(1) }}%
        </span>
      </div>
      <div
        class="text-xs text-zinc-400 font-body rounded-sm px-3 py-2 cursor-pointer"
        style="background:rgba(0,0,0,0.2);border:1px solid var(--border-subtle);"
        :style="{ maxHeight: bestExpanded ? 'none' : '80px', overflow: bestExpanded ? 'visible' : 'hidden' }"
        @click="bestExpanded = !bestExpanded"
      >{{ store.bestPrompt }}</div>
      <div class="flex items-center gap-2 mt-2">
        <button
          @click="applyBestPrompt"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1 rounded-sm"
          style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
        >Apply to Context</button>
        <button
          @click="copyBestPrompt"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1 rounded-sm"
          style="border:1px solid var(--border-subtle);color:var(--zinc-400);"
        >Copy</button>
      </div>
    </div>

    <!-- Generation Timeline -->
    <PromptTimeline
      v-if="store.generations.length > 0"
      :generations="store.generations"
      :current-generation-num="store.progress.generation"
    />

    <!-- No results yet -->
    <div v-else-if="store.isRunning" class="text-xs text-zinc-600 font-body text-center py-8">
      Waiting for first generation...
    </div>
    <div v-else-if="!store.isRunning && store.generations.length === 0" class="text-xs text-zinc-600 font-body text-center py-8">
      No active run.
      <router-link :to="{ name: 'PromptTunerConfig' }" class="text-lime-400 hover:text-lime-300">Start a new tune</router-link>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { usePromptTunerStore } from '../../stores/promptTuner.js'
import { useNotificationsStore } from '../../stores/notifications.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import { useToast } from '../../composables/useToast.js'
import PromptTimeline from '../../components/tool-eval/PromptTimeline.vue'

const store = usePromptTunerStore()
const notifStore = useNotificationsStore()
const { setConfig, setSystemPrompt } = useSharedContext()
const { showToast } = useToast()

const bestExpanded = ref(false)
let unsubscribe = null

onMounted(() => {
  store.restoreJob()

  unsubscribe = notifStore.onMessage((msg) => {
    if (!store.activeJobId) return
    if (msg.job_id && msg.job_id !== store.activeJobId) return

    const tuneTypes = [
      'tune_start', 'generation_start', 'prompt_generated', 'prompt_eval_start',
      'prompt_eval_result', 'generation_complete', 'generation_error', 'tune_complete',
      'job_progress', 'job_completed', 'job_failed', 'job_cancelled',
    ]
    if (tuneTypes.includes(msg.type)) {
      store.handleProgress(msg)

      if (msg.type === 'tune_complete') {
        showToast('Prompt tuning complete!', 'success')
      }
      if (msg.type === 'job_failed') {
        showToast(msg.error || 'Tuning failed', 'error')
      }
    }
  })

  // Load partial results if reconnecting
  if (store.activeRunId && store.generations.length === 0) {
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

function applyBestPrompt() {
  if (!store.bestPrompt) return
  setSystemPrompt('_global', store.bestPrompt)
  setConfig({ lastUpdatedBy: 'prompt_tuner' })
  showToast('Best prompt applied to shared context', 'success')
}

function copyBestPrompt() {
  if (!store.bestPrompt) return
  navigator.clipboard.writeText(store.bestPrompt).then(
    () => showToast('Copied to clipboard', 'success'),
    () => showToast('Failed to copy', 'error')
  )
}
</script>
