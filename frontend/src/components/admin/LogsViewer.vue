<template>
  <div>
    <!-- Filters -->
    <div class="flex items-center gap-3 mb-4 flex-wrap">
      <select
        v-model="level"
        class="text-xs font-mono px-2 py-1 rounded-sm bg-[rgba(255,255,255,0.02)] border border-[var(--border-subtle)] text-zinc-300 outline-none"
        @change="loadLogs"
      >
        <option value="">All Levels</option>
        <option value="DEBUG">DEBUG</option>
        <option value="INFO">INFO</option>
        <option value="WARNING">WARNING</option>
        <option value="ERROR">ERROR</option>
      </select>

      <input
        v-model="search"
        type="text"
        placeholder="Filter logs... (e.g. judge, auto_judge, benchmark)"
        class="px-2 py-1 rounded-sm text-xs font-mono text-zinc-300 flex-1 min-w-[200px]"
        style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
        @keydown.enter="loadLogs"
        @focus="$event.target.style.borderColor = 'rgba(191,255,0,0.3)'"
        @blur="$event.target.style.borderColor = 'var(--border-subtle)'"
      >

      <select
        v-model="lines"
        class="text-xs font-mono px-2 py-1 rounded-sm bg-[rgba(255,255,255,0.02)] border border-[var(--border-subtle)] text-zinc-300 outline-none"
        @change="loadLogs"
      >
        <option :value="50">50 lines</option>
        <option :value="100">100 lines</option>
        <option :value="200">200 lines</option>
        <option :value="500">500 lines</option>
      </select>

      <button
        @click="loadLogs"
        :disabled="loading"
        class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-opacity"
        :class="loading ? 'opacity-50 cursor-not-allowed' : ''"
        style="border:1px solid var(--border-subtle);color:var(--zinc-400);"
      >
        <span v-if="loading">...</span>
        <span v-else>Refresh</span>
      </button>

      <label class="flex items-center gap-1.5 text-[10px] font-display tracking-wider uppercase text-zinc-500 cursor-pointer select-none ml-auto">
        <input type="checkbox" v-model="autoRefresh" class="accent-lime-400" />
        Auto (10s)
      </label>
    </div>

    <!-- Quick filters -->
    <div class="flex items-center gap-2 mb-4">
      <span class="text-[9px] font-display tracking-wider uppercase text-zinc-600">Quick:</span>
      <button
        v-for="qf in quickFilters"
        :key="qf.search"
        @click="applyQuickFilter(qf)"
        class="text-[9px] font-mono px-2 py-0.5 rounded-sm transition-all cursor-pointer"
        :class="search === qf.search && level === (qf.level || '')
          ? 'text-lime-400 border border-lime-400/30 bg-lime-400/5'
          : 'text-zinc-500 border border-zinc-800 hover:text-zinc-300 hover:border-zinc-700'"
      >{{ qf.label }}</button>
    </div>

    <!-- Logs -->
    <div v-if="loading && !entries.length" class="text-zinc-600 text-xs py-3">Loading logs...</div>
    <div v-else-if="!entries.length" class="text-zinc-600 text-sm py-3">No log entries found.</div>
    <div v-else class="overflow-hidden rounded-sm" style="border:1px solid var(--border-subtle);">
      <div class="text-[9px] font-mono text-zinc-600 px-3 py-1.5" style="background:rgba(255,255,255,0.02);border-bottom:1px solid var(--border-subtle);">
        {{ entries.length }} entries
      </div>
      <div ref="logContainer" class="overflow-y-auto font-mono text-[11px] leading-5" style="max-height:600px;background:#0a0a0a;">
        <div
          v-for="(entry, i) in entries"
          :key="i"
          class="px-3 py-0.5 hover:bg-zinc-900/50 border-b border-zinc-900/30"
          :class="entryClass(entry)"
        >
          <span class="text-zinc-600 select-none mr-2">{{ entry.time || '' }}</span>
          <span class="font-bold mr-2" :class="levelColor(entry.level)">{{ entry.level || '' }}</span>
          <span class="text-zinc-500 mr-2">{{ entry.logger || '' }}</span>
          <span class="text-zinc-300">{{ entry.message || '' }}</span>
          <template v-if="entry.user_id">
            <span class="text-zinc-700 ml-2">user={{ entry.user_id.slice(0, 8) }}</span>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { apiFetch } from '../../utils/api.js'

const search = ref('')
const level = ref('')
const lines = ref(100)
const loading = ref(false)
const entries = ref([])
const autoRefresh = ref(true)
const logContainer = ref(null)

let refreshInterval = null

const quickFilters = [
  { label: 'All', search: '', level: '' },
  { label: 'Judge', search: 'judge' },
  { label: 'Auto-Judge', search: 'auto_judge' },
  { label: 'Benchmark', search: 'benchmark' },
  { label: 'Errors', search: '', level: 'ERROR' },
  { label: 'Warnings', search: '', level: 'WARNING' },
  { label: 'Jobs', search: 'job_' },
  { label: 'WebSocket', search: 'ws_' },
]

function applyQuickFilter(qf) {
  search.value = qf.search || ''
  level.value = qf.level || ''
  loadLogs()
}

async function loadLogs() {
  loading.value = true
  try {
    const params = new URLSearchParams({ lines: lines.value })
    if (level.value) params.set('level', level.value)
    if (search.value) params.set('search', search.value)
    const res = await apiFetch(`/api/admin/logs?${params}`)
    if (res.ok) {
      const data = await res.json()
      const raw = data.logs || []
      entries.value = raw.map(parseLogEntry)
      // Scroll to bottom
      await nextTick()
      if (logContainer.value) {
        logContainer.value.scrollTop = logContainer.value.scrollHeight
      }
    }
  } catch { /* ignore */ }
  finally { loading.value = false }
}

function parseLogEntry(raw) {
  try {
    return JSON.parse(raw)
  } catch {
    return { message: raw, level: '', logger: '', time: '' }
  }
}

function levelColor(lvl) {
  switch (lvl) {
    case 'ERROR': return 'text-red-400'
    case 'WARNING': return 'text-yellow-400'
    case 'INFO': return 'text-zinc-500'
    case 'DEBUG': return 'text-zinc-700'
    default: return 'text-zinc-600'
  }
}

function entryClass(entry) {
  if (entry.level === 'ERROR') return 'bg-red-950/20'
  if (entry.level === 'WARNING') return 'bg-yellow-950/10'
  return ''
}

function startAutoRefresh() {
  stopAutoRefresh()
  if (autoRefresh.value) {
    refreshInterval = setInterval(loadLogs, 10000)
  }
}

function stopAutoRefresh() {
  if (refreshInterval) {
    clearInterval(refreshInterval)
    refreshInterval = null
  }
}

// Watch autoRefresh toggle
import { watch } from 'vue'
watch(autoRefresh, (val) => {
  if (val) startAutoRefresh()
  else stopAutoRefresh()
})

onMounted(() => {
  loadLogs()
  startAutoRefresh()
})

onBeforeUnmount(() => {
  stopAutoRefresh()
})
</script>
