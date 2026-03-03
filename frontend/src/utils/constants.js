/**
 * Shared constants extracted from the monolithic index.html.
 */

export const PROVIDER_COLORS = {
  'ZAI GLM':             { bg: 'rgba(191,255,0,0.08)',  border: 'rgba(191,255,0,0.3)',  text: '#BFFF00', bar: '#BFFF00' },
  'Zai':                 { bg: 'rgba(191,255,0,0.08)',  border: 'rgba(191,255,0,0.3)',  text: '#BFFF00', bar: '#BFFF00' },
  'LM Studio (Desktop)': { bg: 'rgba(56,189,248,0.08)', border: 'rgba(56,189,248,0.3)', text: '#38BDF8', bar: '#38BDF8' },
  'LM Studio (Mac)':     { bg: 'rgba(129,140,248,0.08)',border: 'rgba(129,140,248,0.3)',text: '#818CF8', bar: '#818CF8' },
  'LM Studio (Local)':   { bg: 'rgba(56,189,248,0.08)', border: 'rgba(56,189,248,0.3)', text: '#38BDF8', bar: '#38BDF8' },
  'LM Studio':           { bg: 'rgba(56,189,248,0.08)', border: 'rgba(56,189,248,0.3)', text: '#38BDF8', bar: '#38BDF8' },
  'OpenAI':              { bg: 'rgba(168,162,158,0.08)', border: 'rgba(168,162,158,0.3)', text: '#A8A29E', bar: '#A8A29E' },
  'Anthropic':           { bg: 'rgba(251,113,133,0.08)', border: 'rgba(251,113,133,0.3)', text: '#FB7185', bar: '#FB7185' },
  'Google Gemini':       { bg: 'rgba(96,165,250,0.08)',  border: 'rgba(96,165,250,0.3)',  text: '#60A5FA', bar: '#60A5FA' },
  'Together':            { bg: 'rgba(251,146,60,0.08)',  border: 'rgba(251,146,60,0.3)',  text: '#FB923C', bar: '#FB923C' },
  'Ollama':              { bg: 'rgba(74,222,128,0.08)',  border: 'rgba(74,222,128,0.3)',  text: '#4ADE80', bar: '#4ADE80' },
}

export const DEFAULT_COLOR = { bg: 'rgba(251,146,60,0.08)', border: 'rgba(251,146,60,0.3)', text: '#FB923C', bar: '#FB923C' }

/**
 * Get provider color with fuzzy matching.
 * Matches exact name first, then longest substring match.
 */
export function getColor(provider) {
  if (!provider) return DEFAULT_COLOR
  if (PROVIDER_COLORS[provider]) return PROVIDER_COLORS[provider]

  const lower = provider.toLowerCase()
  let best = null
  let bestLen = 0
  for (const [key, val] of Object.entries(PROVIDER_COLORS)) {
    const kl = key.toLowerCase()
    if (lower.includes(kl) && kl.length > bestLen) { best = val; bestLen = kl.length }
    if (kl.includes(lower) && lower.length > bestLen) { best = val; bestLen = lower.length }
  }
  return best || DEFAULT_COLOR
}

// ── HSL helpers for model color variants ──

function hexToHsl(hex) {
  const r = parseInt(hex.slice(1, 3), 16) / 255
  const g = parseInt(hex.slice(3, 5), 16) / 255
  const b = parseInt(hex.slice(5, 7), 16) / 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b)
  let h = 0, s = 0, l = (max + min) / 2
  if (max !== min) {
    const d = max - min
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
    if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6
    else if (max === g) h = ((b - r) / d + 2) / 6
    else h = ((r - g) / d + 4) / 6
  }
  return [h * 360, s * 100, l * 100]
}

function hslToHex(h, s, l) {
  s /= 100; l /= 100
  const a = s * Math.min(l, 1 - l)
  const f = n => {
    const k = (n + h / 30) % 12
    const c = l - a * Math.max(Math.min(k - 3, 9 - k, 1), -1)
    return Math.round(255 * c).toString(16).padStart(2, '0')
  }
  return `#${f(0)}${f(8)}${f(4)}`
}

/**
 * Build a color map: modelKey -> hex color.
 * Models within the same provider get distinct shades of that provider's base hue.
 * Key format: "model_id::provider"
 */
export function buildModelColorMap(results) {
  const providerModels = {}
  for (const r of results) {
    const prov = r.provider || 'unknown'
    const model = r.model_id || r.model || 'unknown'
    if (!providerModels[prov]) providerModels[prov] = new Set()
    providerModels[prov].add(model)
  }

  const colorMap = {}
  for (const [provider, modelSet] of Object.entries(providerModels)) {
    const models = [...modelSet].sort()
    const baseHex = getColor(provider).bar
    const [h, s] = hexToHsl(baseHex)
    const n = models.length

    models.forEach((model, i) => {
      const key = `${model}::${provider}`
      if (n === 1) {
        colorMap[key] = baseHex
      } else {
        // Spread: lightness 38-72%, hue ±18° across models
        const t = i / (n - 1)
        const newL = 38 + t * 34
        const newH = (h - 18 + t * 36 + 360) % 360
        const newS = Math.max(55, Math.min(100, s))
        colorMap[key] = hslToHex(newH, newS, newL)
      }
    })
  }
  return colorMap
}

export const JOB_TYPE_ICONS = {
  benchmark:            '\u{1F680}',
  tool_eval:            '\u{1F527}',
  judge:                '\u2696\uFE0F',
  judge_compare:        '\u2696\uFE0F',
  param_tune:           '\u2699\uFE0F',
  prompt_tune:          '\u{1F4DD}',
  scheduled_benchmark:  '\u23F0',
}

export const JOB_TYPE_LABELS = {
  benchmark:            'Benchmark',
  tool_eval:            'Tool Eval',
  judge:                'Judge',
  judge_compare:        'Judge Compare',
  param_tune:           'Param Tuner',
  prompt_tune:          'Prompt Tuner',
  scheduled_benchmark:  'Scheduled',
}

export const ACTIVE_STATUSES = new Set(['pending', 'running', 'queued'])

export const TIER_OPTIONS = [
  { value: 0, label: '0' },
  { value: 1000, label: '1K' },
  { value: 5000, label: '5K' },
  { value: 10000, label: '10K' },
  { value: 20000, label: '20K' },
  { value: 50000, label: '50K' },
  { value: 100000, label: '100K' },
  { value: 150000, label: '150K' },
]
