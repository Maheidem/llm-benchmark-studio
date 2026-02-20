<template>
  <div class="overflow-x-auto">
    <table class="w-full text-sm results-table">
      <!-- Benchmark headers -->
      <thead v-if="type === 'benchmark'">
        <tr style="border-bottom: 1px solid var(--border-subtle)">
          <th class="px-5 py-3 text-center section-label cursor-pointer" @click="$emit('sort', 'avg_tps')">#</th>
          <th class="px-5 py-3 text-left section-label cursor-pointer" @click="$emit('sort', 'model')">
            Model <SortArrow :active="sortKey === 'model'" :asc="sortAsc" />
          </th>
          <th class="px-5 py-3 text-left section-label cursor-pointer" @click="$emit('sort', 'provider')">
            Provider <SortArrow :active="sortKey === 'provider'" :asc="sortAsc" />
          </th>
          <th class="px-5 py-3 text-right section-label cursor-pointer" @click="$emit('sort', 'avg_tps')">
            Avg TPS <SortArrow :active="sortKey === 'avg_tps'" :asc="sortAsc" />
          </th>
          <th class="px-5 py-3 text-right section-label cursor-pointer" @click="$emit('sort', 'avg_ttft_ms')">
            Avg TTFT <SortArrow :active="sortKey === 'avg_ttft_ms'" :asc="sortAsc" />
          </th>
          <th class="px-5 py-3 text-right section-label cursor-pointer" @click="$emit('sort', 'avg_cost')">
            Avg Cost <SortArrow :active="sortKey === 'avg_cost'" :asc="sortAsc" />
          </th>
          <th class="px-5 py-3 text-center section-label cursor-pointer" @click="$emit('sort', 'total_runs')">
            Runs <SortArrow :active="sortKey === 'total_runs'" :asc="sortAsc" />
          </th>
          <th class="px-5 py-3 text-right section-label cursor-pointer" @click="$emit('sort', 'last_run')">
            Last Run <SortArrow :active="sortKey === 'last_run'" :asc="sortAsc" />
          </th>
        </tr>
      </thead>

      <!-- Tool eval headers -->
      <thead v-else>
        <tr style="border-bottom: 1px solid var(--border-subtle)">
          <th class="px-5 py-3 text-center section-label cursor-pointer" @click="$emit('sort', 'avg_overall_pct')">#</th>
          <th class="px-5 py-3 text-left section-label cursor-pointer" @click="$emit('sort', 'model')">
            Model <SortArrow :active="sortKey === 'model'" :asc="sortAsc" />
          </th>
          <th class="px-5 py-3 text-right section-label cursor-pointer" @click="$emit('sort', 'avg_tool_pct')">
            Avg Tool % <SortArrow :active="sortKey === 'avg_tool_pct'" :asc="sortAsc" />
          </th>
          <th class="px-5 py-3 text-right section-label cursor-pointer" @click="$emit('sort', 'avg_param_pct')">
            Avg Param % <SortArrow :active="sortKey === 'avg_param_pct'" :asc="sortAsc" />
          </th>
          <th class="px-5 py-3 text-right section-label cursor-pointer" @click="$emit('sort', 'avg_overall_pct')">
            Avg Overall % <SortArrow :active="sortKey === 'avg_overall_pct'" :asc="sortAsc" />
          </th>
          <th class="px-5 py-3 text-center section-label cursor-pointer" @click="$emit('sort', 'total_evals')">
            Evals <SortArrow :active="sortKey === 'total_evals'" :asc="sortAsc" />
          </th>
        </tr>
      </thead>

      <tbody>
        <!-- Loading -->
        <tr v-if="loading">
          <td :colspan="type === 'benchmark' ? 8 : 6" class="px-5 py-8 text-center text-zinc-600 text-sm">
            Loading...
          </td>
        </tr>

        <!-- Empty -->
        <tr v-else-if="!sorted.length">
          <td :colspan="type === 'benchmark' ? 8 : 6" class="px-5 py-8 text-center text-zinc-600 text-sm">
            No data yet. Run some {{ type === 'benchmark' ? 'benchmarks' : 'evaluations' }} first!
          </td>
        </tr>

        <!-- Benchmark rows -->
        <template v-else-if="type === 'benchmark'">
          <tr
            v-for="(m, i) in sorted"
            :key="m.model + m.provider"
            :class="{ 'rank-1': i === 0 }"
            style="border-top: 1px solid var(--border-subtle)"
          >
            <td class="px-5 py-3 text-center font-mono text-zinc-500 text-xs">{{ i + 1 }}</td>
            <td class="px-5 py-3 text-left text-zinc-200 text-sm">{{ m.model }}</td>
            <td class="px-5 py-3 text-left text-xs">
              <span
                class="badge"
                :style="{ background: getColor(m.provider).bg, color: getColor(m.provider).text, border: '1px solid ' + getColor(m.provider).border }"
              >{{ m.provider }}</span>
            </td>
            <td
              class="px-5 py-3 text-right font-mono text-sm"
              :style="{ color: i === 0 ? 'var(--lime)' : '#A1A1AA' }"
            >{{ m.avg_tps.toFixed(1) }}</td>
            <td class="px-5 py-3 text-right font-mono text-sm text-zinc-400">{{ m.avg_ttft_ms.toFixed(0) }}ms</td>
            <td class="px-5 py-3 text-right font-mono text-xs text-zinc-500">{{ formatCost(m.avg_cost) }}</td>
            <td class="px-5 py-3 text-center font-mono text-xs text-zinc-500">{{ m.total_runs }}</td>
            <td class="px-5 py-3 text-right font-mono text-xs text-zinc-600">{{ formatDate(m.last_run) }}</td>
          </tr>
        </template>

        <!-- Tool eval rows -->
        <template v-else>
          <tr
            v-for="(m, i) in sorted"
            :key="m.model + m.provider"
            :class="{ 'rank-1': i === 0 }"
            style="border-top: 1px solid var(--border-subtle)"
          >
            <td class="px-5 py-3 text-center font-mono text-zinc-500 text-xs">{{ i + 1 }}</td>
            <td class="px-5 py-3 text-left text-zinc-200 text-sm">{{ m.model }}</td>
            <td class="px-5 py-3 text-right font-mono text-sm text-zinc-400">{{ (m.avg_tool_pct || 0).toFixed(1) }}%</td>
            <td class="px-5 py-3 text-right font-mono text-sm text-zinc-400">{{ (m.avg_param_pct || 0).toFixed(1) }}%</td>
            <td
              class="px-5 py-3 text-right font-mono text-sm"
              :style="{ color: i === 0 ? 'var(--lime)' : '#A1A1AA' }"
            >{{ (m.avg_overall_pct || 0).toFixed(1) }}%</td>
            <td class="px-5 py-3 text-center font-mono text-xs text-zinc-500">{{ m.total_evals || 0 }}</td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useProviderColors } from '../../composables/useProviderColors.js'

const props = defineProps({
  data: { type: Array, default: () => [] },
  type: { type: String, default: 'benchmark' },
  loading: { type: Boolean, default: false },
  sortKey: { type: String, default: 'avg_tps' },
  sortAsc: { type: Boolean, default: false },
})

defineEmits(['sort'])

const { getColor } = useProviderColors()

const sorted = computed(() => {
  if (!props.data.length) return []
  return [...props.data].sort((a, b) => {
    let va = a[props.sortKey]
    let vb = b[props.sortKey]
    if (typeof va === 'string') {
      va = (va || '').toLowerCase()
      vb = (vb || '').toLowerCase()
    }
    if (va < vb) return props.sortAsc ? -1 : 1
    if (va > vb) return props.sortAsc ? 1 : -1
    return 0
  })
})

function formatCost(cost) {
  if (cost != null && cost > 0) return '$' + cost.toFixed(4)
  return 'N/A'
}

function formatDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleDateString()
}

// Minimal sort arrow indicator
const SortArrow = {
  props: { active: Boolean, asc: Boolean },
  template: '<span v-if="active" class="text-[9px] ml-0.5 text-zinc-500">{{ asc ? "\\u25B2" : "\\u25BC" }}</span>',
}
</script>
