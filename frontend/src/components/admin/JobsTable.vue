<template>
  <div class="overflow-x-auto">
    <div v-if="loading" class="text-zinc-600 text-xs py-3">Loading processes...</div>
    <div v-else-if="!jobs.length" class="text-zinc-600 text-xs py-3">No active processes.</div>
    <table v-else class="w-full text-xs">
      <thead>
        <tr style="border-bottom: 1px solid var(--border-subtle)">
          <th class="text-left px-3 py-2 section-label">Type</th>
          <th class="text-left px-3 py-2 section-label">User</th>
          <th class="text-center px-3 py-2 section-label">Status</th>
          <th class="text-right px-3 py-2 section-label">Progress</th>
          <th class="text-left px-3 py-2 section-label">Detail</th>
          <th class="text-left px-3 py-2 section-label">Started</th>
          <th class="text-right px-3 py-2 section-label">Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="j in jobs"
          :key="j.id"
          style="border-bottom: 1px solid var(--border-subtle)"
        >
          <td class="px-3 py-2.5 text-zinc-300">{{ typeLabel(j.job_type) }}</td>
          <td class="px-3 py-2.5 font-mono text-zinc-500">{{ j.user_email || '-' }}</td>
          <td class="px-3 py-2.5 text-center">
            <span
              class="text-[10px] font-display tracking-wider uppercase"
              :style="{ color: statusColor(j.status) }"
            >{{ j.status }}</span>
          </td>
          <td class="px-3 py-2.5 text-right font-mono text-zinc-400">{{ j.progress_pct || 0 }}%</td>
          <td
            class="px-3 py-2.5 text-zinc-500 max-w-[200px] truncate"
            :title="j.progress_detail || '-'"
          >{{ j.progress_detail || '-' }}</td>
          <td class="px-3 py-2.5 text-zinc-500">{{ formatStarted(j) }}</td>
          <td class="px-3 py-2.5 text-right">
            <button
              class="text-[10px] font-display tracking-wider uppercase"
              style="color: var(--coral)"
              @click="$emit('cancel', j.id)"
            >Cancel</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { timeAgo } from '../../utils/helpers.js'

defineProps({
  jobs: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

defineEmits(['cancel'])

const TYPE_LABELS = {
  benchmark: 'Benchmark',
  tool_eval: 'Tool Eval',
  judge: 'Judge',
  judge_compare: 'Judge Compare',
  param_tune: 'Param Tuner',
  prompt_tune: 'Prompt Tuner',
  scheduled_benchmark: 'Scheduled',
}

const STATUS_COLORS = {
  running: '#38BDF8',
  pending: '#85858F',
  queued: '#85858F',
  done: '#22C55E',
  failed: '#FF3B5C',
  cancelled: '#8B8B95',
}

function typeLabel(type) {
  return TYPE_LABELS[type] || type
}

function statusColor(status) {
  return STATUS_COLORS[status] || '#85858F'
}

function formatStarted(job) {
  const ts = job.started_at || job.created_at
  return ts ? timeAgo(ts) : '-'
}
</script>
