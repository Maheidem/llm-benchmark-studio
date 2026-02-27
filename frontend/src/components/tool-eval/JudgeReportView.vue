<template>
  <div v-if="report" class="space-y-6">
    <!-- Overall verdict -->
    <div class="card rounded-md p-5">
      <div class="flex items-center justify-between mb-3">
        <span class="section-label">Judge Report</span>
        <span class="text-xs text-zinc-600 font-body">{{ formatDate(report.timestamp || report.created_at) }}</span>
      </div>

      <div class="flex items-center gap-4 mb-4">
        <div class="text-center">
          <div class="text-3xl font-display font-bold" :style="{ color: gradeColor(report.overall_grade) }">
            {{ report.overall_grade || '?' }}
          </div>
          <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Grade</div>
        </div>
        <div class="text-center">
          <div class="text-2xl font-mono font-bold" :style="{ color: scoreColor(report.overall_score || 0) }">
            {{ report.overall_score || 0 }}
          </div>
          <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Score /100</div>
        </div>
        <div class="flex-1 text-xs text-zinc-400 font-body">
          <div>Judge: <span class="text-zinc-300 font-mono">{{ report.judge_model || 'unknown' }}</span></div>
          <div>Mode: <span class="text-zinc-300">{{ report.mode || 'post_eval' }}</span></div>
          <div v-if="report.eval_run_id">Eval: <span class="text-zinc-500 font-mono">{{ report.eval_run_id?.slice(0, 8) }}</span></div>
        </div>
      </div>
    </div>

    <!-- Per-model reports -->
    <div v-if="modelReports.length > 0" class="space-y-4">
      <div v-for="(mr, i) in modelReports" :key="i" class="card rounded-md p-5">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-2">
            <span class="text-xs font-mono text-zinc-200">{{ mr.model_name || mr.model_id || 'Model' }}</span>
            <span class="text-sm font-display font-bold" :style="{ color: gradeColor(mr.overall_grade) }">
              {{ mr.overall_grade || '?' }}
            </span>
            <span class="text-xs font-mono" :style="{ color: scoreColor(mr.overall_score || 0) }">
              {{ mr.overall_score || 0 }}/100
            </span>
          </div>
          <button
            @click="expandedModels[i] = !expandedModels[i]"
            class="text-[10px] text-zinc-600 hover:text-zinc-400 font-body"
            style="background:none;border:none;cursor:pointer;"
          >{{ expandedModels[i] ? 'Collapse' : 'Details' }}</button>
        </div>

        <!-- Strengths & Weaknesses -->
        <div class="grid grid-cols-2 gap-4 mb-3">
          <div>
            <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1">Strengths</div>
            <ul class="text-xs text-zinc-400 font-body space-y-1">
              <li v-for="s in (mr.strengths || [])" :key="s" class="flex items-start gap-1">
                <span class="text-lime-400 mt-0.5">+</span> {{ s }}
              </li>
              <li v-if="!mr.strengths || mr.strengths.length === 0" class="text-zinc-600">None identified</li>
            </ul>
          </div>
          <div>
            <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1">Weaknesses</div>
            <ul class="text-xs text-zinc-400 font-body space-y-1">
              <li v-for="w in (mr.weaknesses || [])" :key="w" class="flex items-start gap-1">
                <span class="text-red-400 mt-0.5">-</span> {{ w }}
              </li>
              <li v-if="!mr.weaknesses || mr.weaknesses.length === 0" class="text-zinc-600">None identified</li>
            </ul>
          </div>
        </div>

        <!-- Cross-case analysis (expanded) -->
        <div v-if="expandedModels[i]" class="fade-in">
          <div v-if="mr.cross_case_analysis" class="mb-3">
            <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1">Analysis</div>
            <p class="text-xs text-zinc-400 font-body">{{ mr.cross_case_analysis }}</p>
          </div>

          <div v-if="mr.recommendations && mr.recommendations.length > 0">
            <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1">Recommendations</div>
            <ul class="text-xs text-zinc-400 font-body space-y-1">
              <li v-for="r in mr.recommendations" :key="r">{{ r }}</li>
            </ul>
          </div>
        </div>
      </div>
    </div>

    <!-- Verdicts table -->
    <div v-if="verdicts.length > 0" class="card rounded-md overflow-hidden">
      <div class="px-5 py-3 flex items-center justify-between" style="border-bottom:1px solid var(--border-subtle);">
        <span class="section-label">Per-Case Verdicts</span>
        <button
          @click="showVerdicts = !showVerdicts"
          class="text-[10px] text-zinc-600 hover:text-zinc-400 font-body"
          style="background:none;border:none;cursor:pointer;"
        >{{ showVerdicts ? 'Hide' : 'Show' }} ({{ verdicts.length }})</button>
      </div>
      <div v-if="showVerdicts" style="max-height:400px;overflow-y:auto;">
        <table class="w-full text-sm results-table">
          <thead>
            <tr style="border-bottom:1px solid var(--border-subtle);">
              <th class="px-4 py-2 text-left section-label">Case</th>
              <th class="px-3 py-2 text-left section-label">Model</th>
              <th class="px-3 py-2 text-center section-label">Verdict</th>
              <th class="px-3 py-2 text-center section-label">Score</th>
              <th class="px-3 py-2 text-center section-label">Override</th>
              <th class="px-3 py-2 text-left section-label">Summary</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(v, i) in verdicts" :key="i">
              <td class="px-4 py-2 text-xs font-mono text-zinc-500">{{ v.test_case_id || '?' }}</td>
              <td class="px-3 py-2 text-xs font-mono text-zinc-400">{{ v.model_id || '' }}</td>
              <td class="px-3 py-2 text-center">
                <span class="text-[10px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm"
                  :style="verdictStyle(v.verdict)"
                >{{ v.verdict || '?' }}</span>
              </td>
              <td class="px-3 py-2 text-center text-xs font-mono" :style="{ color: qualityScoreColor(v.quality_score) }">
                {{ v.quality_score || 0 }}/5
              </td>
              <td class="px-3 py-2 text-center">
                <template v-if="v.judge_override_score != null">
                  <div class="flex flex-col items-center gap-0.5">
                    <span
                      class="text-xs font-mono font-bold"
                      style="color:#FBBF24;"
                      :title="v.override_reason || 'Judge override'"
                    >{{ (v.judge_override_score * 100).toFixed(0) }}%</span>
                    <span
                      v-if="v.original_score != null"
                      class="text-[10px] font-mono line-through text-zinc-600"
                    >{{ (v.original_score * 100).toFixed(0) }}%</span>
                    <span
                      v-if="v.override_reason"
                      class="text-[9px] text-zinc-600 font-body max-w-[80px] truncate"
                      :title="v.override_reason"
                    >{{ v.override_reason }}</span>
                  </div>
                </template>
                <span v-else class="text-zinc-700 text-xs">—</span>
              </td>
              <td class="px-3 py-2 text-xs text-zinc-500 font-body">{{ v.summary || '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  report: { type: Object, default: null },
})

const showVerdicts = ref(false)
const expandedModels = ref({})

const modelReports = computed(() => {
  if (!props.report) return []
  if (props.report._reports) return props.report._reports
  if (props.report.report_json) {
    try {
      return typeof props.report.report_json === 'string'
        ? JSON.parse(props.report.report_json) : props.report.report_json
    } catch { return [] }
  }
  return []
})

const verdicts = computed(() => {
  if (!props.report) return []
  // _verdicts is set by the store after loading; verdicts comes pre-parsed from the API
  if (props.report._verdicts) return props.report._verdicts
  if (Array.isArray(props.report.verdicts)) return props.report.verdicts
  return []
})

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

function qualityScoreColor(score) {
  if (score >= 4) return 'var(--lime)'
  if (score >= 3) return '#FBBF24'
  return 'var(--coral)'
}

function verdictStyle(verdict) {
  if (verdict === 'pass') return { background: 'rgba(191,255,0,0.1)', color: 'var(--lime)' }
  if (verdict === 'marginal') return { background: 'rgba(234,179,8,0.1)', color: '#EAB308' }
  return { background: 'rgba(255,59,92,0.1)', color: 'var(--coral)' }
}
</script>
