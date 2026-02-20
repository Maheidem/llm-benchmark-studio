/**
 * Shared constants extracted from the monolithic index.html.
 */

export const PROVIDER_COLORS = {
  'ZAI GLM':             { bg: 'rgba(191,255,0,0.08)',  border: 'rgba(191,255,0,0.3)',  text: '#BFFF00', bar: '#BFFF00' },
  'LM Studio (Desktop)': { bg: 'rgba(56,189,248,0.08)', border: 'rgba(56,189,248,0.3)', text: '#38BDF8', bar: '#38BDF8' },
  'LM Studio (Mac)':     { bg: 'rgba(129,140,248,0.08)',border: 'rgba(129,140,248,0.3)',text: '#818CF8', bar: '#818CF8' },
  'LM Studio (Local)':   { bg: 'rgba(56,189,248,0.08)', border: 'rgba(56,189,248,0.3)', text: '#38BDF8', bar: '#38BDF8' },
  'OpenAI':              { bg: 'rgba(168,162,158,0.08)', border: 'rgba(168,162,158,0.3)', text: '#A8A29E', bar: '#A8A29E' },
  'Anthropic':           { bg: 'rgba(251,113,133,0.08)', border: 'rgba(251,113,133,0.3)', text: '#FB7185', bar: '#FB7185' },
  'Google Gemini':       { bg: 'rgba(96,165,250,0.08)',  border: 'rgba(96,165,250,0.3)',  text: '#60A5FA', bar: '#60A5FA' },
}

export const DEFAULT_COLOR = { bg: 'rgba(251,146,60,0.08)', border: 'rgba(251,146,60,0.3)', text: '#FB923C', bar: '#FB923C' }

export function getColor(provider) {
  return PROVIDER_COLORS[provider] || DEFAULT_COLOR
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
