<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Judge Reports</h2>
        <p class="text-sm text-zinc-600 font-body">AI-powered quality assessments of tool calling evaluations.</p>
      </div>
      <div class="flex items-center gap-2">
        <button
          v-if="compareMode"
          @click="runCompare"
          :disabled="selectedForCompare.length !== 2"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
          :class="selectedForCompare.length === 2 ? '' : 'opacity-50 cursor-not-allowed'"
          style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
        >Compare ({{ selectedForCompare.length }}/2)</button>
        <button
          @click="compareMode = !compareMode; selectedForCompare = []"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
          :style="compareMode ? { background: 'rgba(56,189,248,0.08)', border: '1px solid rgba(56,189,248,0.2)', color: '#38BDF8' } : { border: '1px solid var(--border-subtle)', color: 'var(--zinc-400)' }"
        >{{ compareMode ? 'Cancel' : 'Compare' }}</button>
      </div>
    </div>

    <!-- Running indicator -->
    <div v-if="jgStore.isRunning" class="card rounded-md p-5 mb-6">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-3">
          <div class="pulse-dot"></div>
          <span class="text-sm text-zinc-400 font-body">{{ jgStore.progress.detail }}</span>
        </div>
        <span class="text-xs font-mono text-zinc-600">{{ jgStore.progress.pct }}%</span>
      </div>
      <div class="progress-track rounded-full overflow-hidden">
        <div class="progress-fill" :style="{ width: jgStore.progress.pct + '%' }"></div>
      </div>
    </div>

    <div v-if="loading" class="text-xs text-zinc-600 font-body text-center py-8">Loading...</div>

    <div v-else-if="jgStore.reports.length === 0" class="text-xs text-zinc-600 font-body text-center py-8">
      No judge reports yet. Run a judge assessment from the eval history.
    </div>

    <div v-else class="space-y-3">
      <div v-for="report in jgStore.sortedReports" :key="report.id"
        class="card rounded-md px-5 py-4 cursor-pointer hover:bg-white/[0.02] transition-colors"
        :class="{ 'ring-1 ring-blue-400/30': selectedForCompare.includes(report.id) }"
        @click="onReportClick(report)"
      >
        <div class="flex items-center gap-4">
          <!-- Compare checkbox -->
          <div v-if="compareMode" class="flex-none">
            <input
              type="checkbox"
              :checked="selectedForCompare.includes(report.id)"
              @click.stop="toggleCompare(report.id)"
              class="accent-blue-400"
            >
          </div>

          <!-- Grade -->
          <div class="text-center flex-none" style="width:50px;">
            <div class="text-2xl font-display font-bold" :style="{ color: gradeColor(report.overall_grade) }">
              {{ report.overall_grade || '?' }}
            </div>
            <div class="text-[10px] text-zinc-600 font-mono">{{ report.overall_score || 0 }}</div>
          </div>

          <!-- Info -->
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 mb-1">
              <span class="text-xs font-mono text-zinc-300">{{ report.judge_model?.split('/').pop() || 'Judge' }}</span>
              <span class="text-[10px] font-mono px-1.5 py-0.5 rounded-sm"
                :style="statusStyle(report.status)"
              >{{ report.status || 'unknown' }}</span>
              <span class="text-[10px] font-body text-zinc-600">{{ report.mode || '' }}</span>
            </div>
            <div class="text-[10px] text-zinc-600 font-body">
              {{ formatDate(report.timestamp || report.created_at) }}
              <span v-if="report.eval_run_id" class="ml-2">Eval: {{ report.eval_run_id?.slice(0, 8) }}</span>
            </div>
          </div>

          <!-- Actions -->
          <div class="flex items-center gap-2">
            <button
              @click.stop="deleteReport(report)"
              class="text-[10px] text-zinc-600 hover:text-red-400 transition-colors"
              style="background:none;border:none;cursor:pointer;"
              title="Delete report"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Report Detail Modal -->
    <div v-if="selectedReport" class="fixed inset-0 z-50 flex items-center justify-center" style="background:rgba(0,0,0,0.7);" @click.self="selectedReport = null">
      <div class="card rounded-md p-6 max-w-4xl w-full mx-4" style="max-height:85vh;overflow-y:auto;">
        <div class="flex items-center justify-between mb-4">
          <span class="section-label">Report Detail</span>
          <button @click="selectedReport = null" class="text-zinc-500 hover:text-zinc-300" style="background:none;border:none;cursor:pointer;">Close</button>
        </div>
        <JudgeReportView :report="selectedReport" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useJudgeStore } from '../../stores/judge.js'
import { useNotificationsStore } from '../../stores/notifications.js'
import { useToast } from '../../composables/useToast.js'
import JudgeReportView from '../../components/tool-eval/JudgeReportView.vue'

const router = useRouter()
const jgStore = useJudgeStore()
const notifStore = useNotificationsStore()
const { showToast } = useToast()

const loading = ref(true)
const compareMode = ref(false)
const selectedForCompare = ref([])
const selectedReport = ref(null)

let unsubscribe = null

onMounted(async () => {
  try {
    await jgStore.loadReports()
  } catch {
    showToast('Failed to load reports', 'error')
  } finally {
    loading.value = false
  }

  jgStore.restoreJob()

  unsubscribe = notifStore.onMessage((msg) => {
    if (!jgStore.activeJobId) return
    if (msg.job_id && msg.job_id !== jgStore.activeJobId) return

    const judgeTypes = ['judge_start', 'judge_verdict', 'judge_report', 'judge_complete', 'job_progress', 'job_completed', 'job_failed', 'job_cancelled']
    if (judgeTypes.includes(msg.type)) {
      jgStore.handleProgress(msg)

      if (msg.type === 'judge_complete' || msg.type === 'job_completed') {
        showToast('Judge complete!', 'success')
        jgStore.loadReports().catch(() => {})
      }
    }
  })
})

onUnmounted(() => {
  if (unsubscribe) unsubscribe()
})

async function onReportClick(report) {
  if (compareMode.value) {
    toggleCompare(report.id)
    return
  }
  try {
    selectedReport.value = await jgStore.loadReport(report.id)
  } catch {
    showToast('Failed to load report', 'error')
  }
}

function toggleCompare(id) {
  const idx = selectedForCompare.value.indexOf(id)
  if (idx >= 0) {
    selectedForCompare.value.splice(idx, 1)
  } else if (selectedForCompare.value.length < 2) {
    selectedForCompare.value.push(id)
  }
}

function runCompare() {
  if (selectedForCompare.value.length !== 2) return
  router.push({
    name: 'JudgeCompare',
    query: {
      a: selectedForCompare.value[0],
      b: selectedForCompare.value[1],
    },
  })
}

async function deleteReport(report) {
  if (!confirm('Delete this judge report?')) return
  try {
    await jgStore.deleteReport(report.id)
    showToast('Report deleted', 'success')
    if (selectedReport.value?.id === report.id) selectedReport.value = null
  } catch {
    showToast('Failed to delete report', 'error')
  }
}

function formatDate(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function gradeColor(grade) {
  if (!grade) return '#71717A'
  const g = grade.charAt(0).toUpperCase()
  if (g === 'A') return 'var(--lime)'
  if (g === 'B') return '#60A5FA'
  if (g === 'C') return '#FBBF24'
  if (g === 'D') return '#F97316'
  return 'var(--coral)'
}

function statusStyle(status) {
  if (status === 'completed') return { background: 'rgba(191,255,0,0.1)', color: 'var(--lime)' }
  if (status === 'running') return { background: 'rgba(56,189,248,0.1)', color: '#38BDF8' }
  return { background: 'rgba(255,255,255,0.04)', color: '#71717A' }
}
</script>
