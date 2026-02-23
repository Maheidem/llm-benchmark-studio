<template>
  <div class="card rounded-md overflow-hidden">
    <div class="flex items-center justify-between px-5 py-3" style="border-bottom:1px solid var(--border-subtle);">
      <span class="section-label">Summary</span>
      <span class="text-xs text-zinc-600 font-body">{{ summaryInfo }}</span>
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
          <th class="px-5 py-3 text-right section-label">Cases</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!results.length">
          <td colspan="5" class="px-5 py-8 text-center text-zinc-600 text-sm font-body">
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
</script>
