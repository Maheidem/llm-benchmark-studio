<template>
  <div v-if="reportA && reportB" class="space-y-6">
    <!-- Header -->
    <div class="card rounded-md p-5">
      <span class="section-label mb-3 block">Side-by-Side Comparison</span>
      <div class="grid grid-cols-2 gap-6">
        <!-- Report A -->
        <div class="text-center">
          <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1">Report A</div>
          <div class="text-2xl font-display font-bold" :style="{ color: gradeColor(reportA.overall_grade) }">
            {{ reportA.overall_grade || '?' }}
          </div>
          <div class="text-sm font-mono" :style="{ color: scoreColor(reportA.overall_score || 0) }">
            {{ reportA.overall_score || 0 }}/100
          </div>
          <div class="text-[10px] text-zinc-600 font-body mt-1">{{ formatDate(reportA.timestamp || reportA.created_at) }}</div>
        </div>

        <!-- Report B -->
        <div class="text-center">
          <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1">Report B</div>
          <div class="text-2xl font-display font-bold" :style="{ color: gradeColor(reportB.overall_grade) }">
            {{ reportB.overall_grade || '?' }}
          </div>
          <div class="text-sm font-mono" :style="{ color: scoreColor(reportB.overall_score || 0) }">
            {{ reportB.overall_score || 0 }}/100
          </div>
          <div class="text-[10px] text-zinc-600 font-body mt-1">{{ formatDate(reportB.timestamp || reportB.created_at) }}</div>
        </div>
      </div>

      <!-- Score diff -->
      <div class="text-center mt-3">
        <span
          class="text-sm font-mono font-bold"
          :style="{ color: diffColor }"
        >{{ diffLabel }}</span>
      </div>
    </div>

    <!-- Per-model comparison -->
    <div v-for="modelId in commonModels" :key="modelId" class="card rounded-md p-5">
      <div class="text-xs font-mono text-zinc-200 mb-3">{{ modelId }}</div>
      <div class="grid grid-cols-2 gap-4">
        <div v-for="(mr, side) in [getModelReport(reportsA, modelId), getModelReport(reportsB, modelId)]" :key="side"
          class="rounded-sm px-3 py-2"
          :class="side === 0 ? 'border-l-2 border-blue-400/30' : 'border-l-2 border-purple-400/30'"
        >
          <div class="flex items-center gap-2 mb-2">
            <span class="text-sm font-display font-bold" :style="{ color: gradeColor(mr?.overall_grade) }">
              {{ mr?.overall_grade || '?' }}
            </span>
            <span class="text-xs font-mono" :style="{ color: scoreColor(mr?.overall_score || 0) }">
              {{ mr?.overall_score || 0 }}/100
            </span>
          </div>
          <div v-if="mr?.strengths?.length" class="mb-1">
            <span v-for="s in mr.strengths.slice(0, 2)" :key="s" class="text-[10px] text-lime-400/70 font-body block">+ {{ s }}</span>
          </div>
          <div v-if="mr?.weaknesses?.length">
            <span v-for="w in mr.weaknesses.slice(0, 2)" :key="w" class="text-[10px] text-red-400/70 font-body block">- {{ w }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div v-else class="text-xs text-zinc-600 font-body text-center py-8">
    Select two reports to compare
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  reportA: { type: Object, default: null },
  reportB: { type: Object, default: null },
})

const reportsA = computed(() => parseReports(props.reportA))
const reportsB = computed(() => parseReports(props.reportB))

const commonModels = computed(() => {
  const idsA = new Set(reportsA.value.map(r => r.model_id || r.model_name))
  const idsB = new Set(reportsB.value.map(r => r.model_id || r.model_name))
  const all = new Set([...idsA, ...idsB])
  return Array.from(all)
})

const diffColor = computed(() => {
  if (!props.reportA || !props.reportB) return '#71717A'
  const diff = (props.reportA.overall_score || 0) - (props.reportB.overall_score || 0)
  if (diff > 0) return 'var(--lime)'
  if (diff < 0) return 'var(--coral)'
  return '#FBBF24'
})

const diffLabel = computed(() => {
  if (!props.reportA || !props.reportB) return ''
  const diff = (props.reportA.overall_score || 0) - (props.reportB.overall_score || 0)
  if (diff > 0) return `A is +${diff} points ahead`
  if (diff < 0) return `B is +${Math.abs(diff)} points ahead`
  return 'Tied'
})

function parseReports(report) {
  if (!report) return []
  if (report._reports) return report._reports
  if (report.report_json) {
    try {
      return typeof report.report_json === 'string'
        ? JSON.parse(report.report_json) : report.report_json
    } catch { return [] }
  }
  return []
}

function getModelReport(reports, modelId) {
  return reports.find(r => (r.model_id || r.model_name) === modelId) || null
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

function scoreColor(score) {
  if (score >= 80) return 'var(--lime)'
  if (score >= 60) return '#60A5FA'
  if (score >= 40) return '#FBBF24'
  return 'var(--coral)'
}
</script>
