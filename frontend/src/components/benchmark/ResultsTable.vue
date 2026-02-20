<template>
  <div class="card rounded-lg overflow-hidden">
    <div class="overflow-x-auto">
      <table class="w-full results-table">
        <thead>
          <tr class="text-left" style="border-bottom: 1px solid var(--border-subtle);">
            <th class="px-5 py-3 text-center section-label cursor-pointer" @click="sortBy('rank')">
              Rank
            </th>
            <th class="px-5 py-3 section-label cursor-pointer" @click="sortBy('provider')">
              Provider
            </th>
            <th class="px-5 py-3 section-label cursor-pointer" @click="sortBy('model')">
              Model
            </th>
            <th
              v-if="isStressMode"
              class="px-5 py-3 text-right section-label cursor-pointer"
              @click="sortBy('context_tokens')"
            >
              Context
            </th>
            <th class="px-5 py-3 text-right section-label cursor-pointer" @click="sortBy('tokens_per_second')">
              Tok/s
            </th>
            <th
              v-if="multiRun"
              class="px-5 py-3 text-right section-label cursor-pointer"
              @click="sortBy('std_dev_tps')"
            >
              Std Dev
            </th>
            <th class="px-5 py-3 text-right section-label cursor-pointer" @click="sortBy('ttft_ms')">
              TTFT
            </th>
            <th class="px-5 py-3 text-right section-label cursor-pointer" @click="sortBy('input_tokens_per_second')">
              Input Tok/s
            </th>
            <th class="px-5 py-3 text-right section-label cursor-pointer" @click="sortBy('total_time_s')">
              Duration
            </th>
            <th class="px-5 py-3 text-right section-label cursor-pointer" @click="sortBy('output_tokens')">
              Tokens
            </th>
            <th class="px-5 py-3 text-center section-label">Status</th>
            <th class="px-5 py-3 text-right section-label cursor-pointer" @click="sortBy('avg_cost')">
              Cost
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(r, i) in sortedResults"
            :key="r.model_id + '::' + r.provider + '::' + r.context_tokens"
            :class="i === 0 && r.success ? 'rank-1 fade-in' : 'fade-in'"
          >
            <!-- Rank -->
            <td class="px-5 py-3.5 text-center">
              <span
                v-if="i < 3 && r.success"
                class="font-display font-bold"
                :style="i === 0 ? 'color:var(--lime)' : ''"
              >
                {{ POSITION_LABELS[i] }}
              </span>
              <span v-else class="text-zinc-600">{{ i + 1 }}</span>
            </td>

            <!-- Provider -->
            <td class="px-5 py-3.5">
              <ProviderBadge :name="r.provider" />
            </td>

            <!-- Model -->
            <td class="px-5 py-3.5 text-zinc-200 font-medium font-body text-[13px]">
              {{ r.model }}
              <div
                v-if="!r.success && r.error"
                class="text-[11px] font-mono mt-0.5 cursor-pointer"
                style="color:#FB7185;word-break:break-word;max-width:320px;"
                :title="'Click to copy'"
                @click="copyError(r.error)"
              >
                {{ r.error }}
              </div>
            </td>

            <!-- Context (stress mode) -->
            <td v-if="isStressMode" class="px-5 py-3.5 text-right font-mono text-sm text-zinc-500">
              {{ r.context_tokens === 0 ? '0' : formatCtxLabel(r.context_tokens) }}
            </td>

            <!-- Tok/s -->
            <td
              class="px-5 py-3.5 text-right font-mono text-sm"
              :class="r.success && i === 0 ? '' : 'text-zinc-400'"
              :style="r.success && i === 0 ? 'color:var(--lime)' : ''"
            >
              {{ r.success ? r.tokens_per_second.toFixed(1) : '-' }}
            </td>

            <!-- Std Dev -->
            <td v-if="multiRun" class="px-5 py-3.5 text-right font-mono text-sm text-zinc-600">
              {{ r.success && r.std_dev_tps > 0 ? '\u00B1' + r.std_dev_tps.toFixed(1) : '-' }}
            </td>

            <!-- TTFT -->
            <td class="px-5 py-3.5 text-right font-mono text-sm text-zinc-500">
              {{ r.success ? r.ttft_ms.toFixed(0) + 'ms' : '-' }}
            </td>

            <!-- Input Tok/s -->
            <td class="px-5 py-3.5 text-right font-mono text-sm text-zinc-500">
              {{ r.success && r.input_tokens_per_second > 0 ? Math.round(r.input_tokens_per_second).toLocaleString() : '-' }}
            </td>

            <!-- Duration -->
            <td class="px-5 py-3.5 text-right font-mono text-sm text-zinc-500">
              {{ r.success ? r.total_time_s.toFixed(2) + 's' : '-' }}
            </td>

            <!-- Tokens -->
            <td class="px-5 py-3.5 text-right font-mono text-sm text-zinc-500">
              {{ r.success ? r.output_tokens.toFixed(0) : '-' }}
            </td>

            <!-- Status -->
            <td class="px-5 py-3.5 text-center">
              <span v-if="r.failures === 0" class="text-green-500 text-xs font-mono">
                {{ r.runs }}/{{ r.runs }}
              </span>
              <span v-else-if="r.failures < r.runs" class="text-amber-500 text-xs font-mono">
                {{ r.runs - r.failures }}/{{ r.runs }}
              </span>
              <span v-else class="text-red-400 text-xs font-mono" :title="r.error">
                FAIL
              </span>
            </td>

            <!-- Cost -->
            <td class="px-5 py-3.5 text-right font-mono text-sm text-zinc-500">
              {{ r.success && r.avg_cost > 0 ? '$' + r.avg_cost.toFixed(4) : '-' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import ProviderBadge from '../ui/ProviderBadge.vue'
import { formatCtxSize } from '../../utils/helpers.js'

const POSITION_LABELS = ['P1', 'P2', 'P3']

const props = defineProps({
  results: { type: Array, required: true },
  isStressMode: { type: Boolean, default: false },
})

const sortKey = ref('tokens_per_second')
const sortAsc = ref(false)

const multiRun = computed(() => props.results.some(r => r.runs > 1))

const sortedResults = computed(() => {
  if (sortKey.value === 'rank') return props.results

  const key = sortKey.value
  return [...props.results].sort((a, b) => {
    let va = a[key] ?? 0
    let vb = b[key] ?? 0
    if (typeof va === 'string') va = va.toLowerCase()
    if (typeof vb === 'string') vb = vb.toLowerCase()
    if (sortAsc.value) return va > vb ? 1 : va < vb ? -1 : 0
    return va < vb ? 1 : va > vb ? -1 : 0
  })
})

function sortBy(key) {
  if (sortKey.value === key) {
    sortAsc.value = !sortAsc.value
  } else {
    sortKey.value = key
    sortAsc.value = key === 'ttft_ms' || key === 'total_time_s' || key === 'avg_cost'
  }
}

function formatCtxLabel(tokens) {
  return formatCtxSize(tokens).replace(' ctx', '')
}

function copyError(text) {
  navigator.clipboard.writeText(text).catch(() => {})
}
</script>
