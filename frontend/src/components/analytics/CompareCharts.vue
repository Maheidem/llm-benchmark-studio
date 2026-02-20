<template>
  <div class="space-y-6">
    <div class="card rounded-md p-4">
      <h3 class="section-label mb-3">Throughput Comparison (tok/s)</h3>
      <div style="height: 320px">
        <Bar :data="tpsChartData" :options="chartOptions('TOKENS / SECOND', 'tok/s')" />
      </div>
    </div>
    <div class="card rounded-md p-4">
      <h3 class="section-label mb-3">TTFT Comparison (ms)</h3>
      <div style="height: 320px">
        <Bar :data="ttftChartData" :options="chartOptions('TIME TO FIRST TOKEN (MS)', 'ms')" />
      </div>
    </div>

    <!-- Delta table -->
    <div v-if="modelList.length && runs.length >= 2" class="card rounded-md p-4">
      <h3 class="section-label mb-3">Delta Table</h3>
      <div class="overflow-x-auto">
        <table class="w-full text-xs results-table">
          <thead>
            <tr style="border-bottom: 1px solid var(--border-subtle)">
              <th class="px-3 py-2 text-left section-label">Model</th>
              <th
                v-for="(run, ri) in runs"
                :key="'tps-' + ri"
                class="px-3 py-2 text-right section-label"
              >Run {{ ri + 1 }} TPS</th>
              <th
                v-for="(run, ri) in runs"
                :key="'ttft-' + ri"
                class="px-3 py-2 text-right section-label"
              >Run {{ ri + 1 }} TTFT</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="model in modelList" :key="model" style="border-top: 1px solid var(--border-subtle)">
              <td class="px-3 py-2 text-zinc-300">{{ model }}</td>
              <td
                v-for="(run, ri) in runs"
                :key="'tps-val-' + ri"
                class="px-3 py-2 text-right font-mono text-zinc-400"
              >{{ getModelTps(run, model) }}</td>
              <td
                v-for="(run, ri) in runs"
                :key="'ttft-val-' + ri"
                class="px-3 py-2 text-right font-mono text-zinc-400"
              >{{ getModelTtft(run, model) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Bar } from 'vue-chartjs'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Tooltip,
  Legend,
} from 'chart.js'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip, Legend)

const CHART_COLORS = [
  '#BFFF00', '#00d4ff', '#ff6b6b', '#ffd93d',
  '#6bcb77', '#4d96ff', '#ff9f43', '#a855f7',
]

const props = defineProps({
  runs: { type: Array, default: () => [] },
})

const modelList = computed(() => {
  const allModels = new Set()
  props.runs.forEach(run => {
    const items = run.results || run.models || []
    items.forEach(r => allModels.add(r.model || r.display_name))
  })
  return Array.from(allModels).sort()
})

const runLabels = computed(() => {
  return props.runs.map(r => {
    const d = new Date(r.timestamp)
    const prompt = (r.prompt || '').substring(0, 20)
    return d.toLocaleDateString() + (prompt ? ' - ' + prompt : '')
  })
})

const tpsChartData = computed(() => ({
  labels: modelList.value,
  datasets: props.runs.map((run, ri) => {
    const results = run.results || run.models || []
    return {
      label: runLabels.value[ri],
      data: modelList.value.map(m => {
        const match = results.find(r => (r.model || r.display_name) === m)
        return match ? (match.avg_tokens_per_second ?? match.avg_tps ?? 0) : 0
      }),
      backgroundColor: CHART_COLORS[ri % CHART_COLORS.length] + 'CC',
      borderColor: CHART_COLORS[ri % CHART_COLORS.length],
      borderWidth: 0,
      borderRadius: 2,
    }
  }),
}))

const ttftChartData = computed(() => ({
  labels: modelList.value,
  datasets: props.runs.map((run, ri) => {
    const results = run.results || run.models || []
    return {
      label: runLabels.value[ri],
      data: modelList.value.map(m => {
        const match = results.find(r => (r.model || r.display_name) === m)
        return match ? (match.avg_ttft_ms ?? 0) : 0
      }),
      backgroundColor: CHART_COLORS[ri % CHART_COLORS.length] + 'CC',
      borderColor: CHART_COLORS[ri % CHART_COLORS.length],
      borderWidth: 0,
      borderRadius: 2,
    }
  }),
}))

function chartOptions(title, unit) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 800, easing: 'easeOutQuart' },
    plugins: {
      legend: {
        display: true,
        labels: { color: '#85858F', font: { family: 'Outfit', size: 11 }, boxWidth: 12, padding: 16 },
      },
      tooltip: {
        backgroundColor: '#1c1c20',
        borderColor: '#27272A',
        borderWidth: 1,
        titleFont: { family: 'Outfit', size: 13, weight: '500' },
        bodyFont: { family: 'Space Mono', size: 12 },
        padding: 12,
        cornerRadius: 2,
        callbacks: { label: ctx => ` ${ctx.parsed.y.toFixed(1)} ${unit}` },
      },
    },
    scales: {
      x: {
        grid: { color: 'rgba(255,255,255,0.03)', drawBorder: false },
        ticks: { color: '#85858F', font: { family: 'Outfit', size: 11 }, maxRotation: 45 },
      },
      y: {
        grid: { color: 'rgba(255,255,255,0.03)', drawBorder: false },
        ticks: { color: '#8B8B95', font: { family: 'Space Mono', size: 11 } },
        title: { display: true, text: title, color: '#63636B', font: { family: 'Chakra Petch', size: 10, weight: '600' } },
      },
    },
  }
}

function getModelTps(run, model) {
  const items = run.results || run.models || []
  const match = items.find(r => (r.model || r.display_name) === model)
  if (!match) return '-'
  const val = match.avg_tokens_per_second ?? match.avg_tps ?? 0
  return val.toFixed(1)
}

function getModelTtft(run, model) {
  const items = run.results || run.models || []
  const match = items.find(r => (r.model || r.display_name) === model)
  if (!match) return '-'
  const val = match.avg_ttft_ms ?? 0
  return val.toFixed(0) + 'ms'
}
</script>
