<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Judge Reports</h2>
        <p class="text-sm text-zinc-600 font-body">AI-powered quality assessments of tool calling evaluations.</p>
      </div>
      <div class="flex items-center gap-2">
        <!-- Refresh button -->
        <button
          @click="refreshReports"
          :disabled="refreshing"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-opacity"
          :class="refreshing ? 'opacity-50 cursor-not-allowed' : ''"
          style="border:1px solid var(--border-subtle);color:var(--zinc-400);"
          title="Refresh reports"
        >
          <span v-if="refreshing">...</span>
          <span v-else>Refresh</span>
        </button>

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

    <!-- Judge progress (auto-subscribes via store) -->
    <JudgeProgressCard @judge-complete="onJudgeComplete" />

    <div v-if="loading" class="text-xs text-zinc-600 font-body text-center py-8">Loading...</div>

    <div v-else-if="jgStore.reports.length === 0" class="text-xs text-zinc-600 font-body text-center py-8">
      No judge reports yet. Run a judge assessment from the eval history.
    </div>

    <div v-else class="overflow-x-auto">
      <table class="w-full text-xs results-table">
        <thead>
          <tr style="border-bottom:1px solid var(--border-subtle)">
            <th v-if="compareMode" class="px-3 py-3 text-left" style="width:30px;"></th>
            <th class="px-4 py-3 text-left section-label">Date</th>
            <th class="px-4 py-3 text-left section-label">Mode</th>
            <th class="px-4 py-3 text-left section-label">Judge Model</th>
            <th class="px-4 py-3 text-center section-label">Grade</th>
            <th class="px-4 py-3 text-right section-label">Score</th>
            <th class="px-4 py-3 text-center section-label">Ver.</th>
            <th class="px-4 py-3 text-center section-label">Status</th>
            <th class="px-4 py-3 text-right section-label">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="report in jgStore.sortedReports"
            :key="report.id"
            class="cursor-pointer hover:bg-white/[0.02] transition-colors"
            :class="{ 'ring-1 ring-blue-400/30': selectedForCompare.includes(report.id) }"
            style="border-top:1px solid var(--border-subtle)"
            @click="onReportClick(report)"
          >
            <td v-if="compareMode" class="px-3 py-2.5" @click.stop>
              <input
                type="checkbox"
                :checked="selectedForCompare.includes(report.id)"
                @click.stop="toggleCompare(report.id)"
                class="accent-blue-400"
              >
            </td>
            <td class="px-4 py-2.5 text-zinc-400 font-body whitespace-nowrap">
              {{ formatDate(report.timestamp || report.created_at) }}
            </td>
            <td class="px-4 py-2.5 text-zinc-500 font-body">
              {{ report.mode || '-' }}
            </td>
            <td class="px-4 py-2.5 font-mono text-zinc-300">
              {{ report.judge_model?.split('/').pop() || 'Judge' }}
            </td>
            <td class="px-4 py-2.5 text-center">
              <span class="text-lg font-display font-bold" :style="{ color: gradeColor(report.overall_grade) }">
                {{ report.overall_grade || '?' }}
              </span>
            </td>
            <td class="px-4 py-2.5 text-right font-mono text-zinc-400">
              {{ report.overall_score ?? 0 }}
            </td>
            <td class="px-4 py-2.5 text-center">
              <span v-if="report.version && report.version > 1" class="text-[10px] font-mono text-blue-400 px-1 py-0.5 rounded-sm" style="background:rgba(56,189,248,0.08);border:1px solid rgba(56,189,248,0.15);">
                v{{ report.version }}
              </span>
              <span v-else class="text-[10px] font-mono text-zinc-600">v1</span>
            </td>
            <td class="px-4 py-2.5 text-center">
              <span
                class="text-[10px] font-mono px-1.5 py-0.5 rounded-sm"
                :style="statusStyle(report.status)"
              >{{ report.status || 'unknown' }}</span>
            </td>
            <td class="px-4 py-2.5 text-right" @click.stop>
              <div class="flex items-center justify-end gap-2">
                <!-- Re-run button -->
                <button
                  @click.stop="openRerunModal(report)"
                  class="text-[10px] text-zinc-500 hover:text-lime-400 transition-colors"
                  style="background:none;border:none;cursor:pointer;"
                  title="Re-run with new settings"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                  </svg>
                </button>
                <!-- Delete button -->
                <button
                  @click.stop="deleteReport(report)"
                  class="text-[10px] text-zinc-600 hover:text-red-400 transition-colors"
                  style="background:none;border:none;cursor:pointer;"
                  title="Delete report"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Report Detail Modal -->
    <div v-if="selectedReport" class="fixed inset-0 z-50 flex items-center justify-center" style="background:rgba(0,0,0,0.7);" @click.self="selectedReport = null">
      <div class="card rounded-md p-6 max-w-4xl w-full mx-4" style="max-height:85vh;overflow-y:auto;">
        <div class="flex items-center justify-between mb-4">
          <span class="section-label">Report Detail</span>
          <button @click="selectedReport = null" class="text-zinc-500 hover:text-zinc-300" style="background:none;border:none;cursor:pointer;">Close</button>
        </div>

        <!-- Version Chain -->
        <div v-if="reportVersions.length > 1" class="mb-4 p-3 rounded-sm" style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);">
          <p class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-2">Version History</p>
          <div class="flex items-center gap-2 flex-wrap">
            <button
              v-for="v in reportVersions"
              :key="v.id"
              @click="loadVersionReport(v)"
              class="text-[10px] font-mono px-2 py-1 rounded-sm transition-colors"
              :style="v.id === selectedReport.id
                ? { background: 'rgba(191,255,0,0.12)', border: '1px solid rgba(191,255,0,0.3)', color: 'var(--lime)' }
                : { background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-subtle)', color: '#71717A' }"
              :title="formatDate(v.created_at)"
            >
              v{{ v.version || 1 }}
              <span class="ml-1 text-zinc-600">{{ shortDate(v.created_at) }}</span>
            </button>
          </div>
        </div>

        <JudgeReportView :report="selectedReport" />
      </div>
    </div>

    <!-- Re-run Modal -->
    <div v-if="rerunModal.visible" class="fixed inset-0 z-50 flex items-center justify-center" style="background:rgba(0,0,0,0.7);" @click.self="closeRerunModal">
      <div class="card rounded-md p-6 max-w-lg w-full mx-4">
        <div class="flex items-center justify-between mb-4">
          <span class="section-label">Re-run Judge</span>
          <button @click="closeRerunModal" class="text-zinc-500 hover:text-zinc-300" style="background:none;border:none;cursor:pointer;">Cancel</button>
        </div>

        <p class="text-xs text-zinc-500 font-body mb-4">
          Create a new version of this report using different settings. The original is preserved.
        </p>

        <div class="space-y-4">
          <!-- Judge Model -->
          <div>
            <label class="text-[10px] font-display tracking-wider uppercase text-zinc-600 block mb-1">Judge Model</label>
            <select v-model="rerunModal.judge_model" class="settings-select">
              <option value="">-- Use parent model ({{ rerunModal.parentModel }}) --</option>
              <option v-for="m in allModels" :key="m.value" :value="m.value">{{ m.label }}</option>
            </select>
          </div>

          <!-- Custom Instructions -->
          <div>
            <label class="text-[10px] font-display tracking-wider uppercase text-zinc-600 block mb-1">Custom Instructions</label>
            <textarea
              v-model="rerunModal.custom_instructions"
              rows="5"
              class="w-full px-3 py-2 rounded-sm text-xs font-mono text-zinc-200"
              style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;resize:vertical;"
              placeholder="Leave empty to reuse parent instructions, or enter new instructions..."
            ></textarea>
          </div>
        </div>

        <div class="flex justify-end gap-2 mt-5">
          <button
            @click="closeRerunModal"
            class="text-[10px] font-display tracking-wider uppercase px-4 py-2 rounded-sm"
            style="border:1px solid var(--border-subtle);color:#71717A;"
          >Cancel</button>
          <button
            @click="submitRerun"
            :disabled="rerunModal.loading"
            class="text-[10px] font-display tracking-wider uppercase px-4 py-2 rounded-sm transition-opacity"
            :class="rerunModal.loading ? 'opacity-50 cursor-not-allowed' : ''"
            style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
          >
            {{ rerunModal.loading ? 'Submitting...' : 'Re-run' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useJudgeStore } from '../../stores/judge.js'
import { useToast } from '../../composables/useToast.js'
import { apiFetch } from '../../utils/api.js'
import JudgeReportView from '../../components/tool-eval/JudgeReportView.vue'
import JudgeProgressCard from '../../components/ui/JudgeProgressCard.vue'

const router = useRouter()
const jgStore = useJudgeStore()
const { showToast } = useToast()

const loading = ref(true)
const refreshing = ref(false)
const compareMode = ref(false)
const selectedForCompare = ref([])
const selectedReport = ref(null)
const reportVersions = ref([])
const allModels = ref([])

const rerunModal = reactive({
  visible: false,
  loading: false,
  parentReportId: '',
  parentModel: '',
  judge_model: '',
  custom_instructions: '',
})

onMounted(async () => {
  try {
    await jgStore.loadReports()
  } catch {
    showToast('Failed to load reports', 'error')
  } finally {
    loading.value = false
  }

  // Load model list for rerun modal
  loadModels()

  jgStore.restoreJob()
})

// Auto-refresh reports when judge completes (via JudgeProgressCard emit)
async function onJudgeComplete() {
  showToast('Judge complete!', 'success')
  try { await jgStore.loadReports() } catch { showToast('Failed to refresh judge reports', 'error') }
}

async function loadModels() {
  try {
    const res = await apiFetch('/api/config')
    const configData = await res.json()
    const models = []
    for (const [provName, provData] of Object.entries(configData.providers || {})) {
      const pk = provData.provider_key || provName
      const provModels = Array.isArray(provData) ? provData : (provData.models || [])
      for (const m of provModels) {
        const ck = pk + '::' + m.model_id
        models.push({ value: ck, label: `${m.display_name || m.model_id} (${provName})` })
      }
    }
    allModels.value = models
  } catch {
    // non-critical, just means model selector is empty
  }
}

async function refreshReports() {
  refreshing.value = true
  try {
    await jgStore.loadReports()
    showToast('Reports refreshed', 'success')
  } catch {
    showToast('Failed to refresh reports', 'error')
  } finally {
    refreshing.value = false
  }
}

async function onReportClick(report) {
  if (compareMode.value) {
    toggleCompare(report.id)
    return
  }
  try {
    selectedReport.value = await jgStore.loadReport(report.id)
    // Load version chain
    reportVersions.value = []
    const versions = await jgStore.fetchVersions(report.id)
    reportVersions.value = versions
  } catch {
    showToast('Failed to load report', 'error')
  }
}

async function loadVersionReport(version) {
  try {
    selectedReport.value = await jgStore.loadReport(version.id)
  } catch {
    showToast('Failed to load version', 'error')
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

function openRerunModal(report) {
  // Pre-fill with parent report's instructions if available
  // custom_instructions is a plain string field from the API
  const existingInstructions = report.custom_instructions || ''

  rerunModal.visible = true
  rerunModal.loading = false
  rerunModal.parentReportId = report.id
  rerunModal.parentModel = report.judge_model?.split('/').pop() || 'parent model'
  rerunModal.judge_model = ''
  rerunModal.custom_instructions = existingInstructions
}

function closeRerunModal() {
  rerunModal.visible = false
}

async function submitRerun() {
  rerunModal.loading = true
  try {
    // Parse compound key if a model was selected
    let judge_model = null
    let judge_provider_key = null
    if (rerunModal.judge_model) {
      if (rerunModal.judge_model.includes('::')) {
        const i = rerunModal.judge_model.indexOf('::')
        judge_provider_key = rerunModal.judge_model.substring(0, i)
        judge_model = rerunModal.judge_model.substring(i + 2)
      } else {
        judge_model = rerunModal.judge_model
      }
    }

    const body = {
      parent_report_id: rerunModal.parentReportId,
      custom_instructions: rerunModal.custom_instructions || null,
    }
    if (judge_model) body.judge_model = judge_model
    if (judge_provider_key) body.judge_provider_key = judge_provider_key

    await jgStore.rerunJudge(body)
    showToast('Re-run submitted!', 'success')
    closeRerunModal()
  } catch (err) {
    showToast(err.message || 'Failed to submit re-run', 'error')
  } finally {
    rerunModal.loading = false
  }
}

function formatDate(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function shortDate(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
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

<style scoped>
.settings-select {
  width: 100%;
  padding: 8px 12px;
  border-radius: 2px;
  font-size: 13px;
  font-family: 'Outfit', sans-serif;
  color: #E4E4E7;
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border-subtle);
  outline: none;
  transition: border-color 0.2s;
  appearance: auto;
}
.settings-select:focus {
  border-color: rgba(191,255,0,0.3);
}
</style>
