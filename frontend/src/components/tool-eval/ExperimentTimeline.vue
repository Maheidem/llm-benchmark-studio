<template>
  <div class="space-y-3">
    <!-- Filters -->
    <div class="flex items-center gap-2 mb-4">
      <button
        v-for="f in filters"
        :key="f.value"
        @click="activeFilter = activeFilter === f.value ? null : f.value"
        class="text-[10px] font-display tracking-wider uppercase px-2.5 py-1 rounded-sm transition-colors"
        :class="activeFilter === f.value ? 'text-zinc-200' : 'text-zinc-600 hover:text-zinc-400'"
        :style="activeFilter === f.value ? { background: f.bg, border: '1px solid ' + f.border } : { border: '1px solid var(--border-subtle)' }"
      >{{ f.label }}</button>
    </div>

    <div v-if="filteredEntries.length === 0" class="text-xs text-zinc-600 font-body text-center py-8">
      No experiments found
    </div>

    <!-- Grouped by date -->
    <div v-for="group in groupedEntries" :key="group.date" class="mb-4">
      <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-2">{{ group.date }}</div>

      <div v-for="entry in group.entries" :key="entry.id" class="flex items-center gap-3 px-4 py-2.5 rounded-sm mb-1 hover:bg-white/[0.02] cursor-pointer transition-colors"
        style="border:1px solid var(--border-subtle);"
        @click="$emit('navigate', entry)"
      >
        <!-- Type icon -->
        <span class="text-base" :title="typeLabel(entry.type)">{{ typeIcon(entry.type) }}</span>

        <!-- Info -->
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <span class="text-xs font-mono text-zinc-300">{{ typeLabel(entry.type) }}</span>
            <span v-if="entry.config_summary" class="text-[10px] text-zinc-600 font-body truncate">{{ entry.config_summary }}</span>
            <span v-if="entry.prompt_preview" class="text-[10px] text-zinc-600 font-body truncate max-w-[200px]">"{{ entry.prompt_preview }}"</span>
            <span v-if="entry.is_baseline" class="text-[9px] px-1.5 py-0.5 rounded-sm bg-blue-400/10 text-blue-400 font-display tracking-wider uppercase">baseline</span>
            <span v-if="entry.status === 'cancelled'" class="text-[9px] px-1.5 py-0.5 rounded-sm bg-zinc-600/20 text-zinc-500 font-display tracking-wider uppercase">cancelled</span>
          </div>
          <div class="text-[10px] text-zinc-600 font-body">{{ formatTime(entry.timestamp) }}</div>
        </div>

        <!-- Score -->
        <div class="text-right">
          <template v-if="entry.type === 'judge'">
            <span class="text-sm font-display font-bold" :style="{ color: gradeColor(entry.grade) }">
              {{ entry.grade || '?' }}
            </span>
          </template>
          <template v-else-if="entry.score != null">
            <span class="text-xs font-mono font-bold" :style="{ color: scoreColor(entry.score * 100) }">
              {{ (entry.score * 100).toFixed(1) }}%
            </span>
            <span v-if="entry.delta != null" class="text-[10px] font-mono block"
              :style="{ color: entry.delta > 0 ? 'var(--lime)' : entry.delta < 0 ? 'var(--coral)' : '#71717A' }"
            >{{ entry.delta > 0 ? '+' : '' }}{{ (entry.delta * 100).toFixed(1) }}%</span>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { JOB_TYPE_ICONS, JOB_TYPE_LABELS } from '../../utils/constants.js'

const props = defineProps({
  entries: { type: Array, default: () => [] },
})

defineEmits(['navigate'])

const activeFilter = ref(null)

const filters = [
  { value: 'eval', label: 'Evals', bg: 'rgba(56,189,248,0.1)', border: 'rgba(56,189,248,0.3)' },
  { value: 'param_tune', label: 'Param Tune', bg: 'rgba(129,140,248,0.1)', border: 'rgba(129,140,248,0.3)' },
  { value: 'prompt_tune', label: 'Prompt Tune', bg: 'rgba(168,85,247,0.1)', border: 'rgba(168,85,247,0.3)' },
  { value: 'judge', label: 'Judge', bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.3)' },
]

const filteredEntries = computed(() => {
  if (!activeFilter.value) return props.entries
  return props.entries.filter(e => e.type === activeFilter.value)
})

const groupedEntries = computed(() => {
  const groups = {}
  for (const entry of filteredEntries.value) {
    const date = formatDateGroup(entry.timestamp)
    if (!groups[date]) groups[date] = []
    groups[date].push(entry)
  }
  return Object.entries(groups).map(([date, entries]) => ({ date, entries }))
})

function typeIcon(type) {
  const map = {
    eval: JOB_TYPE_ICONS.tool_eval || '',
    param_tune: JOB_TYPE_ICONS.param_tune || '',
    prompt_tune: JOB_TYPE_ICONS.prompt_tune || '',
    judge: JOB_TYPE_ICONS.judge || '',
  }
  return map[type] || ''
}

function typeLabel(type) {
  const map = {
    eval: 'Eval',
    param_tune: 'Param Tune',
    prompt_tune: 'Prompt Tune',
    judge: 'Judge',
  }
  return map[type] || type
}

function formatDateGroup(ts) {
  if (!ts) return 'Unknown'
  const d = new Date(ts)
  const today = new Date()
  if (d.toDateString() === today.toDateString()) return 'Today'
  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
}

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
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
</script>
