<template>
  <div class="card rounded-md overflow-hidden">
    <div class="flex items-center justify-between px-5 py-3" style="border-bottom:1px solid var(--border-subtle);">
      <span class="section-label">Summary</span>
      <div class="flex items-center gap-3">
        <!-- T3: Error type filter -->
        <select
          v-if="allErrorTypes.length > 0"
          v-model="filterErrorType"
          class="text-[10px] font-mono px-2 py-1 rounded-sm"
          style="background:var(--surface);border:1px solid var(--border-subtle);color:#A1A1AA;outline:none;"
        >
          <option value="">All errors</option>
          <option v-for="et in allErrorTypes" :key="et" :value="et">{{ et }}</option>
        </select>
        <span class="text-xs text-zinc-600 font-body">{{ summaryInfo }}</span>
      </div>
    </div>

    <!-- T3: Category breakdown tabs -->
    <div v-if="allCategories.length > 0" class="flex items-center gap-1 px-5 py-2" style="border-bottom:1px solid var(--border-subtle);overflow-x:auto;">
      <button
        :class="['text-[10px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm transition-colors', filterCategory === '' ? 'text-lime-400 border border-lime-400/30 bg-lime-400/05' : 'text-zinc-500 border border-zinc-800 hover:text-zinc-300']"
        @click="filterCategory = ''"
      >All</button>
      <button
        v-for="cat in allCategories"
        :key="cat"
        :class="['text-[10px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm transition-colors', filterCategory === cat ? 'text-lime-400 border border-lime-400/30 bg-lime-400/05' : 'text-zinc-500 border border-zinc-800 hover:text-zinc-300']"
        @click="filterCategory = cat"
      >{{ cat }}</button>
    </div>

    <table class="w-full text-sm results-table">
      <thead>
        <tr style="border-bottom: 1px solid var(--border-subtle)">
          <th class="px-5 py-3 text-left section-label cursor-pointer" @click="sort('model_name')">
            Model {{ sortIndicator('model_name') }}
          </th>
          <th class="px-5 py-3 text-right section-label cursor-pointer" @click="sort('tool_accuracy_pct')">
            Tool % {{ sortIndicator('tool_accuracy_pct') }}
          </th>
          <th class="px-5 py-3 text-right section-label cursor-pointer" @click="sort('param_accuracy_pct')">
            Param % {{ sortIndicator('param_accuracy_pct') }}
          </th>
          <th class="px-5 py-3 text-right section-label cursor-pointer" @click="sort('overall_pct')">
            Overall % {{ sortIndicator('overall_pct') }}
          </th>
          <th v-if="hasIrrelevance" class="px-5 py-3 text-right section-label cursor-pointer" @click="sort('irrelevance_pct')"
            title="Score on cases where model should NOT call any tool">
            Irrel. % {{ sortIndicator('irrelevance_pct') }}
          </th>
          <!-- T1: Format compliance column -->
          <th v-if="hasFormatCompliance" class="px-5 py-3 text-right section-label" title="Format compliance across test cases">
            Format
          </th>
          <!-- T3: Per-category columns -->
          <template v-if="filterCategory === '' && allCategories.length > 0">
            <th v-for="cat in allCategories" :key="'th-' + cat"
              class="px-3 py-3 text-right section-label"
              style="min-width:70px"
            >{{ cat }}</th>
          </template>
          <th class="px-5 py-3 text-right section-label">Cases</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!results.length">
          <td colspan="6" class="px-5 py-8 text-center text-zinc-600 text-sm font-body">
            No results yet.
          </td>
        </tr>
        <tr
          v-for="s in sortedResults"
          :key="s.model_id"
          class="cursor-pointer"
          @click="$emit('showDetail', s.model_id)"
        >
          <td class="px-5 py-3 text-sm font-body text-zinc-200">
            <span>{{ s.model_name || s.model_id }}</span>
            <span
              v-if="s.judge_grade != null"
              class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm ml-2 align-middle"
              style="color:#FBBF24;background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.2)"
              title="Judge analysis available — click to view"
            >Judge: {{ s.judge_grade }}</span>
          </td>
          <td class="px-5 py-3 text-right text-sm font-mono font-bold" :style="{ color: scoreColor(s.tool_accuracy_pct) }">
            {{ (s.tool_accuracy_pct ?? 0).toFixed(1) }}%
          </td>
          <td class="px-5 py-3 text-right text-sm font-mono font-bold" :style="{ color: scoreColor(s.param_accuracy_pct ?? 0) }">
            {{ s.param_accuracy_pct != null ? s.param_accuracy_pct.toFixed(1) + '%' : '-' }}
          </td>
          <td class="px-5 py-3 text-right text-sm font-mono font-bold" :style="{ color: scoreColor(s.overall_pct ?? 0) }">
            {{ (s.overall_pct ?? 0).toFixed(1) }}%
          </td>
          <td v-if="hasIrrelevance" class="px-5 py-3 text-right text-sm font-mono font-bold"
            :style="{ color: s.irrelevance_pct != null ? scoreColor(s.irrelevance_pct) : '#52525B' }"
            :title="s.irrelevance_pct != null ? 'Abstention accuracy on irrelevance test cases' : 'No irrelevance cases'"
          >
            {{ s.irrelevance_pct != null ? s.irrelevance_pct.toFixed(1) + '%' : '-' }}
          </td>
          <!-- T1: Format compliance badge -->
          <td v-if="hasFormatCompliance" class="px-5 py-3 text-right">
            <span
              v-if="s.format_compliance_summary"
              class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm"
              :style="formatComplianceStyle(s.format_compliance_summary)"
              :title="formatComplianceTooltip(s.format_compliance_summary)"
            >{{ s.format_compliance_summary }}</span>
            <span v-else class="text-zinc-600 text-xs">-</span>
          </td>
          <!-- T3: Per-category score columns -->
          <template v-if="filterCategory === '' && allCategories.length > 0">
            <td v-for="cat in allCategories" :key="'td-' + cat + s.model_id"
              class="px-3 py-3 text-right text-xs font-mono"
              :style="{ color: getCategoryScore(s, cat) != null ? scoreColor(getCategoryScore(s, cat)) : '#52525B' }"
            >{{ getCategoryScore(s, cat) != null ? getCategoryScore(s, cat).toFixed(0) + '%' : '-' }}</td>
          </template>
          <td class="px-5 py-3 text-right text-xs font-mono text-zinc-500">{{ s.cases_run || 0 }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  results: { type: Array, required: true },
})

defineEmits(['showDetail'])

const sortKey = ref('overall_pct')
const sortAsc = ref(false)
const filterErrorType = ref('')
const filterCategory = ref('')

function sort(key) {
  if (sortKey.value === key) {
    sortAsc.value = !sortAsc.value
  } else {
    sortKey.value = key
    sortAsc.value = key === 'model_name'
  }
}

function sortIndicator(key) {
  if (sortKey.value !== key) return ''
  return sortAsc.value ? '\u25B2' : '\u25BC'
}

const sortedResults = computed(() => {
  const arr = [...props.results]
  arr.sort((a, b) => {
    const aVal = a[sortKey.value] ?? 0
    const bVal = b[sortKey.value] ?? 0
    if (typeof aVal === 'string') {
      return sortAsc.value ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
    }
    return sortAsc.value ? aVal - bVal : bVal - aVal
  })
  return arr
})

// Show irrelevance column only if at least one result has the field
const hasIrrelevance = computed(() => {
  return props.results.some(r => r.irrelevance_pct != null)
})

// T1: Show format compliance column if any result has it
const hasFormatCompliance = computed(() => {
  return props.results.some(r => r.format_compliance_summary != null)
})

// T3: Collect all categories across results
const allCategories = computed(() => {
  const cats = new Set()
  for (const r of props.results) {
    const breakdown = r.category_breakdown || {}
    for (const k of Object.keys(breakdown)) cats.add(k)
  }
  return Array.from(cats).sort()
})

// T2: Collect all error types across results
const allErrorTypes = computed(() => {
  const types = new Set()
  for (const r of props.results) {
    const breakdown = r.error_type_breakdown || {}
    for (const k of Object.keys(breakdown)) types.add(k)
  }
  return Array.from(types).sort()
})

const summaryInfo = computed(() => {
  const models = props.results.length
  const totalCases = props.results.reduce((sum, r) => sum + (r.cases_run || 0), 0)
  return `${totalCases} evaluations across ${models} model${models > 1 ? 's' : ''}`
})

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
}

// T1: Format compliance badge styles
function formatComplianceStyle(val) {
  if (!val) return {}
  const v = val.toUpperCase()
  if (v === 'PASS') return { background: 'rgba(191,255,0,0.08)', color: 'var(--lime)', border: '1px solid rgba(191,255,0,0.2)' }
  if (v === 'NORMALIZED') return { background: 'rgba(251,191,36,0.08)', color: '#FBBF24', border: '1px solid rgba(251,191,36,0.2)' }
  return { background: 'rgba(255,59,92,0.08)', color: 'var(--coral)', border: '1px solid rgba(255,59,92,0.2)' }
}

function formatComplianceTooltip(val) {
  if (!val) return ''
  const v = val.toUpperCase()
  if (v === 'PASS') return 'All responses matched expected format exactly'
  if (v === 'NORMALIZED') return 'Some responses required normalization (e.g., case correction) to match'
  return 'Some responses failed format requirements'
}

// T3: Get per-category score for a result
function getCategoryScore(result, category) {
  const breakdown = result.category_breakdown || {}
  const val = breakdown[category]
  if (val == null) return null
  // Support both raw fraction (0-1) and percentage (0-100)
  return val <= 1 ? val * 100 : val
}
</script>
