<template>
  <div v-if="hasData" class="card p-4 rounded-lg">
    <div class="section-label mb-3">Speed vs Latency</div>
    <div style="height: 320px;">
      <Scatter :data="chartData" :options="chartOptions" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Scatter } from 'vue-chartjs'
import {
  Chart as ChartJS,
  LinearScale,
  PointElement,
  Tooltip,
  Legend,
} from 'chart.js'
import { useChartTheme } from '../../composables/useChartTheme.js'
import { getColor } from '../../utils/constants.js'

ChartJS.register(LinearScale, PointElement, Tooltip, Legend)

const props = defineProps({
  results: { type: Array, required: true },
})

const { gridColor, tooltipStyle, axisLabelFont, tickFont } = useChartTheme()

const successfulResults = computed(() => props.results.filter(a => a.success))
const hasData = computed(() => successfulResults.value.length >= 2)

const chartData = computed(() => {
  const agg = successfulResults.value
  if (agg.length < 2) return { datasets: [] }

  const allTokens = agg.map(a => a.output_tokens)
  const minT = Math.min(...allTokens)
  const maxT = Math.max(...allTokens)
  const scaleSize = (t) => maxT === minT ? 10 : 5 + (t - minT) / (maxT - minT) * 15

  const datasets = agg.map(a => {
    const color = getColor(a.provider)
    return {
      label: a.model,
      data: [{ x: a.tokens_per_second, y: a.ttft_ms }],
      backgroundColor: color.bar,
      borderColor: color.bar,
      pointRadius: scaleSize(a.output_tokens),
      pointHoverRadius: scaleSize(a.output_tokens) + 3,
    }
  })

  return { datasets }
})

const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 800, easing: 'easeOutQuart' },
  plugins: {
    legend: {
      display: true,
      position: 'top',
      labels: {
        color: '#A1A1AA',
        font: { family: "'Outfit'", size: 11 },
        boxWidth: 8,
        boxHeight: 8,
        padding: 12,
        usePointStyle: true,
      },
    },
    tooltip: {
      ...tooltipStyle,
      callbacks: {
        title: c => c[0].dataset.label,
        label: c => [` Tok/s: ${c.parsed.x.toFixed(1)}`, ` TTFT: ${c.parsed.y.toFixed(0)}ms`],
      },
    },
  },
  scales: {
    x: {
      grid: { color: gridColor, drawBorder: false },
      ticks: { color: '#8B8B95', font: tickFont },
      title: { display: true, text: 'TOKENS / SECOND', color: '#63636B', font: axisLabelFont, padding: { top: 12 } },
    },
    y: {
      grid: { color: gridColor, drawBorder: false },
      ticks: { color: '#8B8B95', font: tickFont },
      title: { display: true, text: 'TTFT (MS)', color: '#63636B', font: axisLabelFont, padding: { right: 12 } },
    },
  },
}))
</script>
