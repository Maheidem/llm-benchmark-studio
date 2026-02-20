<template>
  <div class="card p-4 rounded-lg">
    <div class="section-label mb-3">
      {{ isStressMode ? 'Latency (TTFT) vs Context Size' : 'Time to First Token (TTFT)' }}
    </div>
    <div :style="{ height: chartHeight + 'px' }">
      <component :is="chartComponent" :data="chartData" :options="chartOptions" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Bar, Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js'
import { useChartTheme } from '../../composables/useChartTheme.js'
import { getColor } from '../../utils/constants.js'

ChartJS.register(CategoryScale, LinearScale, BarElement, LineElement, PointElement, Tooltip, Legend, Filler)

const props = defineProps({
  results: { type: Array, required: true },
  isStressMode: { type: Boolean, default: false },
})

const { gridColor, tooltipStyle, axisLabelFont, tickFont } = useChartTheme()

const chartComponent = computed(() => props.isStressMode ? Line : Bar)

const chartHeight = computed(() => {
  if (props.isStressMode) return 320
  const successful = props.results.filter(a => a.success)
  return Math.max(200, successful.length * 36)
})

const chartData = computed(() => {
  const agg = props.results

  if (!props.isStressMode) {
    const sorted = [...agg].filter(a => a.success).sort((a, b) => a.ttft_ms - b.ttft_ms)
    return {
      labels: sorted.map(a => a.model),
      datasets: [{
        label: 'TTFT (ms)',
        data: sorted.map(a => a.ttft_ms),
        backgroundColor: sorted.map(a => getColor(a.provider).bar + 'CC'),
        borderWidth: 0,
        borderRadius: 2,
        barPercentage: 0.55,
      }],
    }
  }

  // Stress mode: line chart
  const tiers = [...new Set(agg.map(a => a.context_tokens))].sort((a, b) => a - b)
  const models = [...new Set(agg.map(a => `${a.model_id}::${a.provider}`))]
  const tierLabels = tiers.map(t => t === 0 ? '0' : (t >= 1000 ? (t / 1000) + 'K' : String(t)))

  const datasets = models.map(uid => {
    const modelResults = agg.filter(a => `${a.model_id}::${a.provider}` === uid)
    const modelName = modelResults[0]?.model || uid
    const provider = modelResults[0]?.provider || ''
    const color = getColor(provider)
    const data = tiers.map(tier => {
      const m = modelResults.find(r => r.context_tokens === tier)
      return m && m.success ? m.ttft_ms : null
    })
    return {
      label: modelName,
      data,
      borderColor: color.bar,
      backgroundColor: color.bar + '33',
      borderWidth: 2,
      pointRadius: 4,
      pointBackgroundColor: color.bar,
      pointBorderColor: '#09090B',
      pointBorderWidth: 2,
      tension: 0.3,
      spanGaps: false,
    }
  })

  return { labels: tierLabels, datasets }
})

const chartOptions = computed(() => {
  const base = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 800, easing: 'easeOutQuart' },
  }

  if (!props.isStressMode) {
    return {
      ...base,
      indexAxis: 'y',
      plugins: {
        legend: { display: false },
        tooltip: {
          ...tooltipStyle,
          callbacks: {
            label: c => ` ${c.parsed.x.toFixed(0)} ms`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: gridColor, drawBorder: false },
          ticks: { color: '#8B8B95', font: tickFont },
          title: { display: true, text: 'TIME TO FIRST TOKEN (MS)', color: '#63636B', font: axisLabelFont, padding: { top: 12 } },
        },
        y: {
          grid: { display: false, drawBorder: false },
          ticks: { color: '#A1A1AA', font: { family: "'Outfit'", size: 12, weight: '500' } },
        },
      },
    }
  }

  // Stress mode options
  return {
    ...base,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        display: true,
        position: 'top',
        labels: { color: '#A1A1AA', font: { family: "'Outfit'", size: 11 }, boxWidth: 12, boxHeight: 2, padding: 16 },
      },
      tooltip: {
        ...tooltipStyle,
        callbacks: {
          title: c => `Context: ${c[0].label} tokens`,
          label: c => c.parsed.y !== null ? ` ${c.dataset.label}: ${c.parsed.y.toFixed(0)}ms` : ` ${c.dataset.label}: N/A`,
        },
      },
    },
    scales: {
      x: {
        grid: { color: gridColor, drawBorder: false },
        ticks: { color: '#8B8B95', font: tickFont },
        title: { display: true, text: 'CONTEXT SIZE (TOKENS)', color: '#63636B', font: axisLabelFont, padding: { top: 12 } },
      },
      y: {
        grid: { color: gridColor, drawBorder: false },
        ticks: { color: '#8B8B95', font: tickFont },
        title: { display: true, text: 'TIME TO FIRST TOKEN (MS)', color: '#63636B', font: axisLabelFont, padding: { right: 12 } },
      },
    },
  }
})
</script>
