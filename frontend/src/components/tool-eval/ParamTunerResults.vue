<template>
  <div class="card rounded-md overflow-hidden mb-6">
    <div class="px-5 py-3 flex items-center justify-between" style="border-bottom:1px solid var(--border-subtle);">
      <span class="section-label">Results</span>
      <span class="text-xs font-mono text-zinc-600">{{ results.length }} combo{{ results.length !== 1 ? 's' : '' }}</span>
    </div>
    <div style="max-height:500px;overflow-y:auto;">
      <table class="w-full text-sm results-table">
        <thead>
          <tr style="border-bottom:1px solid var(--border-subtle);">
            <th class="px-4 py-2 text-left section-label cursor-pointer" @click="$emit('sort', 'model_name')">Model</th>
            <th v-for="param in paramColumns" :key="param"
              class="px-3 py-2 text-center section-label cursor-pointer"
              @click="$emit('sort', 'config.' + param)"
            >{{ formatParam(param) }}</th>
            <th class="px-3 py-2 text-right section-label cursor-pointer" @click="$emit('sort', 'overall_score')">Score</th>
            <th class="px-3 py-2 text-right section-label cursor-pointer" @click="$emit('sort', 'tool_accuracy')">Tool%</th>
            <th class="px-3 py-2 text-right section-label cursor-pointer" @click="$emit('sort', 'param_accuracy')">Param%</th>
            <th class="px-3 py-2 text-right section-label">Latency</th>
            <th class="px-3 py-2 text-center section-label">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(r, i) in results"
            :key="i"
            class="hover:bg-white/[0.02] cursor-pointer transition-colors"
            :class="{ 'bg-lime-400/[0.03]': isBest(r) }"
            @click="$emit('select', r)"
          >
            <td class="px-4 py-2 text-xs font-mono text-zinc-300">
              <div class="flex items-center gap-1.5">
                <span v-if="isBest(r)" class="text-lime-400 text-[10px]" title="Best config">*</span>
                {{ r.model_name || r.model_id || '' }}
              </div>
            </td>
            <td v-for="param in paramColumns" :key="param" class="px-3 py-2 text-center text-xs font-mono text-zinc-400">
              <div class="flex items-center justify-center gap-1">
                <span>{{ formatValue(r.config?.[param]) }}</span>
                <span
                  v-if="hasAdjustment(r, param, 'dropped')"
                  class="text-[9px] px-1 py-0.5 rounded-sm bg-red-400/10 text-red-400"
                  title="Dropped (not supported)"
                >dropped</span>
                <span
                  v-if="hasAdjustment(r, param, 'clamped')"
                  class="text-[9px] px-1 py-0.5 rounded-sm bg-yellow-400/10 text-yellow-400"
                  :title="getClampTitle(r, param)"
                >clamped</span>
              </div>
            </td>
            <td class="px-3 py-2 text-right text-xs font-mono font-bold" :style="{ color: scoreColor(r.overall_score * 100) }">
              {{ (r.overall_score * 100).toFixed(0) }}%
            </td>
            <td class="px-3 py-2 text-right text-xs font-mono text-zinc-400">
              {{ r.tool_accuracy?.toFixed(0) || '0' }}%
            </td>
            <td class="px-3 py-2 text-right text-xs font-mono text-zinc-400">
              {{ r.param_accuracy?.toFixed(0) || '0' }}%
            </td>
            <td class="px-3 py-2 text-right text-xs font-mono text-zinc-600">
              {{ r.latency_avg_ms ? r.latency_avg_ms + 'ms' : '-' }}
            </td>
            <td class="px-3 py-2 text-center text-xs font-mono">
              <span :style="{ color: passColor(r.cases_passed, r.cases_total) }">
                {{ r.cases_passed || 0 }}/{{ r.cases_total || 0 }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  results: { type: Array, default: () => [] },
  bestOverallScore: { type: Number, default: 0 },
})

defineEmits(['sort', 'select'])

const paramColumns = computed(() => {
  const keys = new Set()
  for (const r of props.results) {
    if (r.config && typeof r.config === 'object') {
      for (const k of Object.keys(r.config)) {
        keys.add(k)
      }
    }
  }
  return Array.from(keys).sort()
})

function formatParam(name) {
  const names = {
    temperature: 'Temp',
    top_p: 'Top P',
    top_k: 'Top K',
    tool_choice: 'TC',
    repetition_penalty: 'Rep Pen',
    min_p: 'Min P',
    frequency_penalty: 'Freq Pen',
    presence_penalty: 'Pres Pen',
  }
  return names[name] || name.replace(/_/g, ' ')
}

function formatValue(val) {
  if (val === undefined || val === null) return '-'
  if (typeof val === 'number') return Number.isInteger(val) ? val.toString() : val.toFixed(3)
  return String(val)
}

function isBest(r) {
  return props.bestOverallScore > 0 && r.overall_score === props.bestOverallScore
}

function hasAdjustment(r, param, type) {
  if (!r.adjustments || !Array.isArray(r.adjustments)) return false
  return r.adjustments.some(a => a.param === param && a.type === type)
}

function getClampTitle(r, param) {
  if (!r.adjustments) return ''
  const adj = r.adjustments.find(a => a.param === param && a.type === 'clamped')
  if (!adj) return ''
  return `Clamped from ${adj.original} to ${adj.clamped}`
}

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
}

function passColor(passed, total) {
  if (!total) return 'var(--zinc-600)'
  const ratio = passed / total
  if (ratio >= 0.8) return 'var(--lime)'
  if (ratio >= 0.5) return '#FBBF24'
  return 'var(--coral)'
}
</script>
