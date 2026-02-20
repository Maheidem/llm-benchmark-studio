<template>
  <div class="overflow-x-auto">
    <table class="w-full text-sm results-table">
      <thead>
        <tr style="border-bottom: 1px solid var(--border-subtle)">
          <th class="px-5 py-3 text-left section-label">Name</th>
          <th class="px-5 py-3 text-center section-label">Models</th>
          <th class="px-5 py-3 text-center section-label">Interval</th>
          <th class="px-5 py-3 text-right section-label">Last Run</th>
          <th class="px-5 py-3 text-right section-label">Next Run</th>
          <th class="px-5 py-3 text-center section-label">Status</th>
          <th class="px-5 py-3 text-right section-label">Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading">
          <td colspan="7" class="px-5 py-8 text-center text-zinc-600 text-sm">Loading...</td>
        </tr>
        <tr v-else-if="!schedules.length">
          <td colspan="7" class="px-5 py-8 text-center text-zinc-600 text-sm">
            No schedules yet. Create one to automate recurring benchmarks.
          </td>
        </tr>
        <tr
          v-for="s in schedules"
          :key="s.id"
          style="border-top: 1px solid var(--border-subtle)"
        >
          <td class="px-5 py-3 text-zinc-200 text-sm">{{ s.name }}</td>
          <td class="px-5 py-3 text-center font-mono text-xs text-zinc-400">{{ modelCount(s) }}</td>
          <td class="px-5 py-3 text-center text-xs text-zinc-400">{{ formatInterval(s.interval_hours) }}</td>
          <td class="px-5 py-3 text-right text-xs font-mono text-zinc-500">{{ formatSchedTime(s.last_run) }}</td>
          <td class="px-5 py-3 text-right text-xs font-mono text-zinc-500">{{ formatSchedTime(s.next_run) }}</td>
          <td class="px-5 py-3 text-center">
            <label class="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                :checked="!!s.enabled"
                class="sr-only peer"
                @change="$emit('toggle-enabled', s.id, $event.target.checked)"
              />
              <div class="w-8 h-4 rounded-full peer bg-zinc-700 peer-checked:bg-[rgba(191,255,0,0.4)] after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-zinc-400 after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:after:translate-x-4 peer-checked:after:bg-[var(--lime)]"></div>
            </label>
          </td>
          <td class="px-5 py-3 text-right">
            <div class="flex items-center justify-end gap-2">
              <button
                class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300 transition-colors px-2 py-1 rounded-sm border border-[var(--border-subtle)]"
                @click="$emit('trigger', s.id)"
              >Run Now</button>
              <button
                class="text-[10px] font-display tracking-wider uppercase text-zinc-600 hover:text-red-400 transition-colors px-2 py-1 rounded-sm border border-[var(--border-subtle)]"
                @click="$emit('delete', s.id, s.name)"
              >Del</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
defineProps({
  schedules: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

defineEmits(['toggle-enabled', 'trigger', 'delete'])

const SCHED_INTERVALS = [
  { value: 1, label: 'Every hour' },
  { value: 6, label: 'Every 6 hours' },
  { value: 12, label: 'Every 12 hours' },
  { value: 24, label: 'Every day' },
  { value: 168, label: 'Every week' },
]

function modelCount(schedule) {
  const models = schedule.models || []
  if (typeof schedule.models_json === 'string') {
    try { return JSON.parse(schedule.models_json).length } catch { return 0 }
  }
  return models.length
}

function formatInterval(hours) {
  const m = SCHED_INTERVALS.find(i => i.value === hours)
  if (m) return m.label
  if (hours < 24) return `Every ${hours}h`
  if (hours % 168 === 0) return `Every ${hours / 168}w`
  if (hours % 24 === 0) return `Every ${hours / 24}d`
  return `Every ${hours}h`
}

function formatSchedTime(iso) {
  if (!iso) return '--'
  const d = new Date(iso)
  const now = new Date()
  const diff = d.getTime() - now.getTime()

  if (Math.abs(diff) < 60000) return 'just now'

  const absDiff = Math.abs(diff)
  if (absDiff < 3600000) {
    const mins = Math.round(absDiff / 60000)
    return diff > 0 ? `in ${mins}m` : `${mins}m ago`
  }
  if (absDiff < 86400000) {
    const hrs = Math.round(absDiff / 3600000)
    return diff > 0 ? `in ${hrs}h` : `${hrs}h ago`
  }

  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
</script>
