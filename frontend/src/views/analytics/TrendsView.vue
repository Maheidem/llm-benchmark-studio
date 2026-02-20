<template>
  <div>
    <!-- Controls -->
    <div class="flex items-center gap-3 mb-5 flex-wrap">
      <!-- Model selector dropdown -->
      <div class="relative" ref="dropdownRef">
        <button
          class="text-xs font-display tracking-wider uppercase px-3 py-1.5 rounded-sm border border-[var(--border-subtle)] text-zinc-400 hover:text-zinc-300 transition-colors flex items-center gap-2"
          @click="dropdownOpen = !dropdownOpen"
        >
          <span>{{ modelLabel }}</span>
          <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
        <div
          v-if="dropdownOpen"
          class="absolute top-full left-0 mt-1 w-64 max-h-60 overflow-y-auto rounded-sm z-20"
          style="background: var(--surface); border: 1px solid var(--border-subtle); scrollbar-width: thin"
        >
          <div v-if="loadingModels" class="text-zinc-600 text-xs px-3 py-2">Loading models...</div>
          <div v-else-if="!availableModels.length" class="text-zinc-600 text-xs px-3 py-2">No models found.</div>
          <label
            v-for="m in availableModels"
            :key="m.model"
            class="flex items-center gap-2 px-2 py-1 rounded-sm cursor-pointer hover:bg-white/[0.03] text-xs text-zinc-400"
          >
            <input
              type="checkbox"
              :value="m.model"
              :checked="selectedModels.has(m.model)"
              class="accent-lime-400 w-3 h-3"
              @change="toggleModel(m.model, $event.target.checked)"
            />
            {{ m.model }}
          </label>
        </div>
      </div>

      <!-- Period filter -->
      <select
        v-model="period"
        class="text-xs font-mono px-2 py-1 rounded-sm bg-[rgba(255,255,255,0.02)] border border-[var(--border-subtle)] text-zinc-300 outline-none"
        @change="loadTrends"
      >
        <option value="7d">Last 7 days</option>
        <option value="30d">Last 30 days</option>
        <option value="90d">Last 90 days</option>
        <option value="all">All time</option>
      </select>
    </div>

    <!-- Charts -->
    <div v-if="tpsData && ttftData && hasSeries">
      <TrendsCharts :tps-data="tpsData" :ttft-data="ttftData" />
    </div>
    <div v-else-if="loading" class="text-zinc-600 text-sm text-center py-8">
      Loading trends...
    </div>
    <div v-else class="text-zinc-600 text-sm text-center py-8">
      Select one or more models above to view performance trends over time.
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { apiFetch } from '../../utils/api.js'
import { useToast } from '../../composables/useToast.js'
import TrendsCharts from '../../components/analytics/TrendsCharts.vue'

const { showToast } = useToast()

const availableModels = ref([])
const loadingModels = ref(false)
const selectedModels = reactive(new Set())
const period = ref('all')
const tpsData = ref(null)
const ttftData = ref(null)
const loading = ref(false)
const dropdownOpen = ref(false)
const dropdownRef = ref(null)

const modelLabel = computed(() => {
  const n = selectedModels.size
  if (n === 0) return 'Select Models'
  return n + ' model' + (n > 1 ? 's' : '') + ' selected'
})

const hasSeries = computed(() => {
  return (tpsData.value?.series?.length > 0) || (ttftData.value?.series?.length > 0)
})

function toggleModel(model, checked) {
  if (checked) {
    selectedModels.add(model)
  } else {
    selectedModels.delete(model)
  }
  if (selectedModels.size > 0) loadTrends()
}

async function loadModels() {
  loadingModels.value = true
  try {
    const res = await apiFetch('/api/analytics/leaderboard?type=benchmark&period=all')
    if (!res.ok) return
    const data = await res.json()
    availableModels.value = data.models || []
  } catch {
    // ignore
  } finally {
    loadingModels.value = false
  }
}

async function loadTrends() {
  if (selectedModels.size === 0) {
    tpsData.value = null
    ttftData.value = null
    return
  }

  loading.value = true
  const models = Array.from(selectedModels).join(',')

  try {
    const [resTps, resTtft] = await Promise.all([
      apiFetch(`/api/analytics/trends?models=${encodeURIComponent(models)}&metric=tps&period=${period.value}`),
      apiFetch(`/api/analytics/trends?models=${encodeURIComponent(models)}&metric=ttft&period=${period.value}`),
    ])

    tpsData.value = await resTps.json()
    ttftData.value = await resTtft.json()
  } catch {
    showToast('Failed to load trends', 'error')
  } finally {
    loading.value = false
  }
}

function handleClickOutside(e) {
  if (dropdownRef.value && !dropdownRef.value.contains(e.target)) {
    dropdownOpen.value = false
  }
}

onMounted(() => {
  loadModels()
  document.addEventListener('click', handleClickOutside)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>
