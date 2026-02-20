<template>
  <div>
    <!-- Controls -->
    <div class="flex items-center gap-3 mb-5 flex-wrap">
      <!-- Type toggle -->
      <div class="flex gap-1">
        <button
          v-for="t in typeOptions"
          :key="t.value"
          :class="[
            'text-[11px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-all',
            leaderboardType === t.value
              ? 'text-[var(--lime)] border border-[rgba(191,255,0,0.3)] bg-[var(--lime-dim)]'
              : 'text-zinc-500 border border-[var(--border-subtle)] bg-transparent hover:text-zinc-300'
          ]"
          @click="setType(t.value)"
        >
          {{ t.label }}
        </button>
      </div>

      <!-- Period filter -->
      <select
        v-model="period"
        class="text-xs font-mono px-2 py-1 rounded-sm bg-[rgba(255,255,255,0.02)] border border-[var(--border-subtle)] text-zinc-300 outline-none"
        @change="loadLeaderboard"
      >
        <option value="7d">Last 7 days</option>
        <option value="30d">Last 30 days</option>
        <option value="90d">Last 90 days</option>
        <option value="all">All time</option>
      </select>
    </div>

    <!-- Table -->
    <LeaderboardTable
      :data="data"
      :type="leaderboardType"
      :loading="loading"
      :sort-key="sortKey"
      :sort-asc="sortAsc"
      @sort="handleSort"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { apiFetch } from '../../utils/api.js'
import LeaderboardTable from '../../components/analytics/LeaderboardTable.vue'

const typeOptions = [
  { value: 'benchmark', label: 'Benchmark' },
  { value: 'tool_eval', label: 'Tool Eval' },
]

const leaderboardType = ref('benchmark')
const period = ref('all')
const data = ref([])
const loading = ref(false)
const sortKey = ref('avg_tps')
const sortAsc = ref(false)

function setType(type) {
  leaderboardType.value = type
  sortKey.value = type === 'benchmark' ? 'avg_tps' : 'avg_overall_pct'
  sortAsc.value = false
  loadLeaderboard()
}

function handleSort(key) {
  if (sortKey.value === key) {
    sortAsc.value = !sortAsc.value
  } else {
    sortKey.value = key
    sortAsc.value = false
  }
}

async function loadLeaderboard() {
  loading.value = true
  try {
    const res = await apiFetch(`/api/analytics/leaderboard?type=${leaderboardType.value}&period=${period.value}`)
    if (!res.ok) throw new Error('Failed to load')
    const json = await res.json()
    data.value = json.models || []
  } catch {
    data.value = []
  } finally {
    loading.value = false
  }
}

onMounted(loadLeaderboard)
</script>
