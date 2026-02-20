import { Chart } from 'chart.js'

export function useChartTheme() {
  Chart.defaults.color = '#8B8B95'
  Chart.defaults.borderColor = '#1f1f23'
  Chart.defaults.font.family = "'Outfit', sans-serif"

  const gridColor = 'rgba(255,255,255,0.03)'

  const tooltipStyle = {
    backgroundColor: '#1c1c20',
    borderColor: '#27272A',
    borderWidth: 1,
    titleFont: { family: "'Outfit'", weight: '500', size: 13 },
    bodyFont: { family: "'Space Mono'", size: 12 },
    titleColor: '#E4E4E7',
    bodyColor: '#A1A1AA',
    padding: 12,
    cornerRadius: 2,
  }

  const axisLabelFont = {
    family: "'Chakra Petch'",
    size: 10,
    weight: '600',
  }

  const tickFont = {
    family: "'Space Mono'",
    size: 11,
  }

  return { gridColor, tooltipStyle, axisLabelFont, tickFont }
}
