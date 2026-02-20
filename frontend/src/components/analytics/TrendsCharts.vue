<template>
  <div class="space-y-6">
    <div v-if="tpsDatasets.length" class="card rounded-md p-4">
      <h3 class="section-label mb-3">Throughput Over Time</h3>
      <div style="height: 320px">
        <Line :data="{ datasets: tpsDatasets }" :options="lineOptions('TOKENS / SECOND')" />
      </div>
    </div>
    <div v-if="ttftDatasets.length" class="card rounded-md p-4">
      <h3 class="section-label mb-3">TTFT Over Time</h3>
      <div style="height: 320px">
        <Line :data="{ datasets: ttftDatasets }" :options="lineOptions('TTFT (MS)')" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
  Legend,
  TimeScale,
} from 'chart.js'
import 'chartjs-adapter-date-fns'

ChartJS.register(CategoryScale, LinearScale, LineElement, PointElement, Tooltip, Legend, TimeScale)

const CHART_COLORS = [
  '#BFFF00', '#00d4ff', '#ff6b6b', '#ffd93d',
  '#6bcb77', '#4d96ff', '#ff9f43', '#a855f7',
]

const props = defineProps({
  tpsData: { type: Object, default: () => ({ series: [] }) },
  ttftData: { type: Object, default: () => ({ series: [] }) },
})

function buildDatasets(data) {
  return (data?.series || []).map((s, i) => ({
    label: s.model,
    data: (s.points || []).map(p => ({ x: new Date(p.timestamp), y: p.value })),
    borderColor: CHART_COLORS[i % CHART_COLORS.length],
    backgroundColor: CHART_COLORS[i % CHART_COLORS.length] + '33',
    borderWidth: 2,
    pointRadius: 3,
    pointBackgroundColor: CHART_COLORS[i % CHART_COLORS.length],
    pointBorderColor: '#09090B',
    pointBorderWidth: 2,
    tension: 0.3,
    fill: false,
  }))
}

const tpsDatasets = computed(() => buildDatasets(props.tpsData))
const ttftDatasets = computed(() => buildDatasets(props.ttftData))

function lineOptions(yTitle) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 800, easing: 'easeOutQuart' },
    interaction: { mode: 'index', intersect: false },
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
      },
    },
    scales: {
      x: {
        type: 'time',
        time: { unit: 'day', tooltipFormat: 'MMM d, yyyy HH:mm' },
        grid: { color: 'rgba(255,255,255,0.03)', drawBorder: false },
        ticks: { color: '#8B8B95', font: { family: 'Space Mono', size: 10 } },
      },
      y: {
        grid: { color: 'rgba(255,255,255,0.03)', drawBorder: false },
        ticks: { color: '#8B8B95', font: { family: 'Space Mono', size: 11 } },
        title: { display: true, text: yTitle, color: '#63636B', font: { family: 'Chakra Petch', size: 10, weight: '600' } },
      },
    },
  }
}
</script>
