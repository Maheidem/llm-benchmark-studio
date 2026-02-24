<template>
  <div class="card rounded-md overflow-hidden mb-6">
    <div class="px-5 py-3 flex items-center justify-between" style="border-bottom:1px solid var(--border-subtle);">
      <span class="section-label">Quality Correlation</span>
      <div class="flex items-center gap-2">
        <!-- View toggle: scatter / table -->
        <div class="flex gap-1">
          <button
            v-for="v in viewOptions"
            :key="v.value"
            :class="[
              'text-[10px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm transition-colors',
              viewMode === v.value ? 'text-lime-400 border border-lime-400/30' : 'text-zinc-500 border border-zinc-800 hover:text-zinc-300'
            ]"
            @click="viewMode = v.value"
          >{{ v.label }}</button>
        </div>
        <!-- Filter by model -->
        <select
          v-if="allModels.length > 1"
          v-model="filterModel"
          class="text-[10px] font-mono px-2 py-0.5 rounded-sm"
          style="background:var(--surface);border:1px solid var(--border-subtle);color:#A1A1AA;outline:none;"
        >
          <option value="">All models</option>
          <option v-for="m in allModels" :key="m" :value="m">{{ m }}</option>
        </select>
        <span class="text-[10px] text-zinc-600 font-body">{{ filteredData.length }} configs</span>
      </div>
    </div>

    <!-- Scatter / Bubble chart -->
    <div v-if="viewMode === 'scatter'" class="p-4">
      <div class="text-[10px] text-zinc-600 font-body mb-2">
        X: tokens/sec (throughput) &nbsp;|&nbsp; Y: quality score (judge) &nbsp;|&nbsp; Size: cost
      </div>
      <div class="relative" style="height:280px;overflow:hidden;">
        <svg class="w-full h-full" viewBox="0 0 400 260" preserveAspectRatio="xMidYMid meet">
          <!-- Axes -->
          <line x1="40" y1="10" x2="40" y2="230" stroke="var(--border-subtle)" stroke-width="1"/>
          <line x1="40" y1="230" x2="390" y2="230" stroke="var(--border-subtle)" stroke-width="1"/>

          <!-- Axis labels -->
          <text x="215" y="255" text-anchor="middle" class="axis-label" font-size="8" fill="#52525B">Throughput (tok/s)</text>
          <text x="12" y="120" text-anchor="middle" class="axis-label" font-size="8" fill="#52525B" transform="rotate(-90, 12, 120)">Quality Score</text>

          <!-- Pareto frontier line -->
          <polyline
            v-if="paretoPoints.length > 1"
            :points="paretoPoints.map(p => `${scaleX(p.throughput)},${scaleY(p.quality)}`).join(' ')"
            fill="none"
            stroke="rgba(191,255,0,0.3)"
            stroke-width="1"
            stroke-dasharray="3,2"
          />

          <!-- Data points -->
          <g v-for="(d, i) in plotData" :key="i">
            <circle
              :cx="scaleX(d.throughput)"
              :cy="scaleY(d.quality)"
              :r="bubbleRadius(d.cost)"
              :fill="d.isPareto ? 'rgba(191,255,0,0.15)' : 'rgba(56,189,248,0.1)'"
              :stroke="d.isPareto ? 'var(--lime)' : 'rgba(56,189,248,0.4)'"
              stroke-width="1"
              class="cursor-pointer"
              @click="$emit('select', d.result)"
              @mouseenter="hoveredIndex = i"
              @mouseleave="hoveredIndex = null"
            />
            <!-- Pareto star marker -->
            <text
              v-if="d.isPareto"
              :x="scaleX(d.throughput)"
              :y="scaleY(d.quality) - bubbleRadius(d.cost) - 2"
              text-anchor="middle"
              font-size="7"
              fill="var(--lime)"
            >&#9733;</text>
          </g>

          <!-- Tooltip -->
          <g v-if="hoveredIndex != null && plotData[hoveredIndex]">
            <rect
              :x="Math.min(scaleX(plotData[hoveredIndex].throughput) + 6, 310)"
              :y="scaleY(plotData[hoveredIndex].quality) - 30"
              width="80" height="28"
              rx="2"
              fill="rgba(9,9,11,0.9)"
              stroke="var(--border-subtle)"
            />
            <text
              :x="Math.min(scaleX(plotData[hoveredIndex].throughput) + 10, 314)"
              :y="scaleY(plotData[hoveredIndex].quality) - 18"
              font-size="7" fill="#E4E4E7"
            >{{ plotData[hoveredIndex].label }}</text>
            <text
              :x="Math.min(scaleX(plotData[hoveredIndex].throughput) + 10, 314)"
              :y="scaleY(plotData[hoveredIndex].quality) - 8"
              font-size="7" fill="#A1A1AA"
            >Q:{{ (plotData[hoveredIndex].quality * 100).toFixed(0) }}% T:{{ plotData[hoveredIndex].throughput?.toFixed(0) || '?' }}</text>
          </g>
        </svg>
      </div>

      <!-- Legend -->
      <div class="flex items-center gap-4 mt-2 text-[9px] font-body text-zinc-600">
        <span class="flex items-center gap-1">
          <span class="inline-block w-2 h-2 rounded-full border" style="border-color:var(--lime);background:rgba(191,255,0,0.15);"></span>
          Pareto optimal
        </span>
        <span class="flex items-center gap-1">
          <span class="inline-block w-2 h-2 rounded-full border" style="border-color:rgba(56,189,248,0.4);background:rgba(56,189,248,0.1);"></span>
          Other configs
        </span>
        <span>Bubble size = relative cost</span>
      </div>
    </div>

    <!-- Table view -->
    <div v-else style="max-height:400px;overflow-y:auto;">
      <table class="w-full text-xs results-table">
        <thead>
          <tr style="border-bottom:1px solid var(--border-subtle);">
            <th class="px-4 py-2 text-left section-label">Model</th>
            <th class="px-3 py-2 text-right section-label cursor-pointer" @click="tableSort('throughput')">
              Tok/s {{ tableSortIndicator('throughput') }}
            </th>
            <th class="px-3 py-2 text-right section-label cursor-pointer" @click="tableSort('quality')">
              Quality {{ tableSortIndicator('quality') }}
            </th>
            <th class="px-3 py-2 text-right section-label cursor-pointer" @click="tableSort('cost')">
              Cost {{ tableSortIndicator('cost') }}
            </th>
            <th class="px-3 py-2 text-center section-label">Pareto</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(d, i) in sortedTableData"
            :key="i"
            class="cursor-pointer hover:bg-white/[0.02] transition-colors"
            @click="$emit('select', d.result)"
          >
            <td class="px-4 py-2 text-xs font-mono text-zinc-300">
              <div class="flex items-center gap-1">
                <span v-if="d.isPareto" class="text-lime-400 text-[10px]" title="Pareto optimal">&#9733;</span>
                {{ d.label }}
              </div>
            </td>
            <td class="px-3 py-2 text-right font-mono text-zinc-400">{{ d.throughput?.toFixed(1) || '-' }}</td>
            <td class="px-3 py-2 text-right font-mono font-bold" :style="{ color: qualityColor(d.quality) }">
              {{ d.quality != null ? (d.quality * 100).toFixed(1) + '%' : '-' }}
            </td>
            <td class="px-3 py-2 text-right font-mono text-zinc-400">
              {{ d.cost != null ? '$' + d.cost.toFixed(4) : '-' }}
            </td>
            <td class="px-3 py-2 text-center">
              <span v-if="d.isPareto" style="color:var(--lime)">&#9733;</span>
              <span v-else class="text-zinc-700">-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  // Array of { result, throughput, quality, cost, model_name, config_label }
  data: { type: Array, default: () => [] },
})

defineEmits(['select'])

const viewMode = ref('scatter')
const filterModel = ref('')
const hoveredIndex = ref(null)
const tableSortKey = ref('quality')
const tableSortAsc = ref(false)

const viewOptions = [
  { value: 'scatter', label: 'Scatter' },
  { value: 'table', label: 'Table' },
]

const allModels = computed(() => {
  const names = new Set(props.data.map(d => d.model_name).filter(Boolean))
  return Array.from(names).sort()
})

const filteredData = computed(() => {
  if (!filterModel.value) return props.data
  return props.data.filter(d => d.model_name === filterModel.value)
})

// Pareto frontier: configs that are not dominated on both throughput and quality
const paretoPoints = computed(() => {
  const valid = filteredData.value.filter(d => d.throughput != null && d.quality != null)
  const sorted = [...valid].sort((a, b) => b.throughput - a.throughput)
  const frontier = []
  let bestQuality = -1
  for (const d of sorted) {
    if (d.quality > bestQuality) {
      bestQuality = d.quality
      frontier.push(d)
    }
  }
  return frontier.sort((a, b) => a.throughput - b.throughput)
})

const paretoSet = computed(() => new Set(paretoPoints.value.map(d => d.result)))

// Scale helpers for scatter plot (viewBox 40-390 x, 10-230 y)
const xMin = computed(() => Math.min(...filteredData.value.map(d => d.throughput || 0)) * 0.9)
const xMax = computed(() => Math.max(...filteredData.value.map(d => d.throughput || 0)) * 1.1 || 100)
const costMax = computed(() => Math.max(...filteredData.value.map(d => d.cost || 0)) || 1)

function scaleX(val) {
  const range = xMax.value - xMin.value || 1
  return 40 + ((val - xMin.value) / range) * 350
}

function scaleY(val) {
  // quality 0-1, y inverted (higher = up)
  return 230 - (val || 0) * 220
}

function bubbleRadius(cost) {
  if (!cost || !costMax.value) return 4
  return 3 + (cost / costMax.value) * 8
}

const plotData = computed(() => {
  return filteredData.value.map(d => ({
    ...d,
    label: d.config_label || d.model_name || '',
    isPareto: paretoSet.value.has(d.result),
  }))
})

// Table sorting
function tableSort(key) {
  if (tableSortKey.value === key) {
    tableSortAsc.value = !tableSortAsc.value
  } else {
    tableSortKey.value = key
    tableSortAsc.value = false
  }
}

function tableSortIndicator(key) {
  if (tableSortKey.value !== key) return ''
  return tableSortAsc.value ? '\u25B2' : '\u25BC'
}

const sortedTableData = computed(() => {
  return [...filteredData.value]
    .map(d => ({ ...d, isPareto: paretoSet.value.has(d.result) }))
    .sort((a, b) => {
      const va = a[tableSortKey.value] ?? 0
      const vb = b[tableSortKey.value] ?? 0
      return tableSortAsc.value ? va - vb : vb - va
    })
})

function qualityColor(val) {
  if (val == null) return '#52525B'
  if (val >= 0.8) return 'var(--lime)'
  if (val >= 0.5) return '#FBBF24'
  return 'var(--coral)'
}
</script>
