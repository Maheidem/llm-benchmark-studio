<template>
  <div>
    <!-- Run selection -->
    <div class="mb-5">
      <div class="flex items-center justify-between mb-3">
        <span class="section-label">Select 2-4 runs to compare</span>
        <button
          :disabled="selectedRuns.size < 2"
          :class="[
            'text-[11px] font-display tracking-wider uppercase px-4 py-1.5 rounded-sm transition-all',
            selectedRuns.size >= 2
              ? 'text-[var(--lime)] border border-[rgba(191,255,0,0.3)] cursor-pointer opacity-100'
              : 'text-zinc-500 border border-[var(--border-subtle)] cursor-not-allowed opacity-50'
          ]"
          @click="loadComparison"
        >
          Compare
        </button>
      </div>

      <div v-if="loadingRuns" class="text-zinc-600 text-xs py-3">Loading recent runs...</div>
      <div v-else-if="!runs.length" class="text-zinc-600 text-xs py-3">No benchmark runs found.</div>
      <div v-else class="flex flex-col gap-1 max-h-64 overflow-y-auto pr-1" style="scrollbar-width: thin">
        <label
          v-for="run in runs.slice(0, 20)"
          :key="run.id"
          class="flex items-center gap-3 px-3 py-2 rounded-sm cursor-pointer transition-colors hover:bg-white/[0.02]"
          style="border: 1px solid var(--border-subtle)"
        >
          <input
            type="checkbox"
            :value="run.id"
            :checked="selectedRuns.has(run.id)"
            class="accent-lime-400 w-3.5 h-3.5"
            @change="toggleRun(run.id, $event.target.checked)"
          />
          <span class="text-xs font-mono text-zinc-500">{{ formatTimestamp(run.timestamp) }}</span>
          <span class="text-xs text-zinc-400 truncate flex-1">{{ (run.prompt || 'Benchmark run').substring(0, 50) }}</span>
          <span class="text-[10px] font-mono text-zinc-600">{{ (run.results || []).length }} models</span>
        </label>
      </div>
    </div>

    <!-- Charts -->
    <div v-if="comparisonData.length">
      <CompareCharts :runs="comparisonData" />
    </div>
    <div v-else-if="!loadingComparison" class="text-zinc-600 text-sm text-center py-8">
      Select at least 2 benchmark runs above, then click Compare.
    </div>
    <div v-if="loadingComparison" class="text-zinc-600 text-sm text-center py-8">
      Loading comparison...
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { apiFetch } from '../../utils/api.js'
import { useToast } from '../../composables/useToast.js'
import CompareCharts from '../../components/analytics/CompareCharts.vue'

const { showToast } = useToast()

const runs = ref([])
const loadingRuns = ref(false)
const selectedRuns = reactive(new Set())
const comparisonData = ref([])
const loadingComparison = ref(false)

function formatTimestamp(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleString()
}

function toggleRun(id, checked) {
  if (checked) {
    if (selectedRuns.size >= 4) {
      showToast('Max 4 runs can be compared', 'error')
      return
    }
    selectedRuns.add(id)
  } else {
    selectedRuns.delete(id)
  }
}

async function loadRuns() {
  loadingRuns.value = true
  try {
    const res = await apiFetch('/api/history')
    const data = await res.json()
    runs.value = (data.runs || []).filter(r => r.id)
  } catch {
    runs.value = []
  } finally {
    loadingRuns.value = false
  }
}

async function loadComparison() {
  if (selectedRuns.size < 2) return
  loadingComparison.value = true
  const ids = Array.from(selectedRuns).join(',')

  try {
    const res = await apiFetch(`/api/analytics/compare?runs=${ids}`)
    if (!res.ok) throw new Error('Failed')
    const data = await res.json()
    comparisonData.value = data.runs || []
  } catch {
    // Fallback: fetch runs individually
    try {
      const results = await Promise.all(
        Array.from(selectedRuns).map(async id => {
          const r = await apiFetch(`/api/history/${id}`)
          return r.json()
        })
      )
      comparisonData.value = results
    } catch {
      showToast('Failed to load comparison data', 'error')
      comparisonData.value = []
    }
  } finally {
    loadingComparison.value = false
  }
}

onMounted(loadRuns)
</script>
