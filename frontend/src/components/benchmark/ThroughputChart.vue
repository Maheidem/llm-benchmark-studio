<template>
  <div class="card p-4 rounded-lg">
    <div class="section-label mb-3">
      {{ isStressMode ? 'Throughput vs Context Size' : 'Throughput (Tokens/sec)' }}
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
  return Math.max(200, props.results.length * 36)
})

const chartData = computed(() => {
  const agg = props.results

  if (!props.isStressMode) {
    return {
      labels: agg.map(a => a.model),
      datasets: [{
        label: 'Tokens/sec',
        data: agg.map(a => a.tokens_per_second),
        backgroundColor: agg.map(a => getColor(a.provider).bar + 'CC'),
        borderColor: agg.map(a => getColor(a.provider).bar),
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
      const match = modelResults.find(r => r.context_tokens === tier)
      return match && match.success ? match.tokens_per_second : null
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
            label: ctx => ` ${ctx.parsed.x.toFixed(1)} tok/s`,
            afterLabel: ctx => {
              const r = props.results[ctx.dataIndex]
              return r ? ` TTFT: ${r.ttft_ms.toFixed(0)}ms  |  Tokens: ${r.output_tokens.toFixed(0)}` : ''
            },
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
          title: ctx => `Context: ${ctx[0].label} tokens`,
          label: ctx => ctx.parsed.y !== null ? ` ${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)} tok/s` : ` ${ctx.dataset.label}: N/A`,
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
        title: { display: true, text: 'TOKENS / SECOND', color: '#63636B', font: axisLabelFont, padding: { right: 12 } },
      },
    },
  }
})
</script>
