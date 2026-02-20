<template>
  <div v-if="hasContent && !hidden" class="te-context-bar" style="position:relative;">
    <span class="text-[10px] text-zinc-600 font-display tracking-wider uppercase">Context:</span>

    <span v-if="ctx.suiteId" class="ctx-pill">{{ ctx.suiteName || 'Suite' }}</span>

    <span v-if="ctx.selectedModels.length > 0" class="ctx-pill">
      {{ ctx.selectedModels.length }} model{{ ctx.selectedModels.length > 1 ? 's' : '' }}
    </span>

    <span
      v-if="ctx.experimentId"
      class="ctx-pill active"
      style="cursor:pointer;"
      @click="$emit('toggleExperiment')"
    >{{ ctx.experimentName || 'Experiment' }}</span>

    <button
      v-if="ctx.suiteId && !ctx.experimentId"
      @click="$emit('newExperiment')"
      class="text-[10px] px-1.5 py-0.5 rounded-sm"
      style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);cursor:pointer;"
      title="New Experiment"
    >+</button>

    <span
      v-if="hasSystemPrompt"
      class="ctx-pill"
      style="background:rgba(168,85,247,0.1);border-color:rgba(168,85,247,0.25);color:#A855F7;cursor:pointer;"
      @click="$emit('showSystemPrompt')"
    >{{ systemPromptLabel }}</span>

    <span
      v-if="ctx.lastUpdatedBy"
      class="ctx-pill"
      :style="appliedFromStyle"
    >{{ appliedFromLabel }}</span>

    <button
      @click="hidden = true"
      class="ml-auto text-[10px] text-zinc-700 hover:text-zinc-500"
      style="background:none;border:none;cursor:pointer;"
    >Hide</button>
  </div>

  <button
    v-if="hasContent && hidden"
    @click="hidden = false"
    class="text-[10px] text-zinc-700 hover:text-zinc-500 px-4 py-1"
    style="background:none;border:none;cursor:pointer;"
  >Show Context</button>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useSharedContext } from '../../composables/useSharedContext.js'

defineEmits(['toggleExperiment', 'newExperiment', 'showSystemPrompt'])

const { context: ctx } = useSharedContext()
const hidden = ref(false)

const hasContent = computed(() => {
  return ctx.suiteId || ctx.selectedModels.length > 0 || ctx.experimentId
})

const hasSystemPrompt = computed(() => {
  const sp = ctx.systemPrompts
  if (!sp) return false
  if (typeof sp === 'string') return sp.trim().length > 0
  if (typeof sp === 'object') {
    return Object.values(sp).some(v => v && v.trim())
  }
  return false
})

const systemPromptLabel = computed(() => {
  const sp = ctx.systemPrompts
  if (!sp || typeof sp !== 'object') return 'System Prompt'
  let perModelCount = 0
  for (const [k, v] of Object.entries(sp)) {
    if (v && v.trim() && k !== '_global') perModelCount++
  }
  return perModelCount > 0
    ? `System Prompt (${perModelCount} model${perModelCount > 1 ? 's' : ''})`
    : 'System Prompt'
})

const appliedFromLabels = {
  param_tuner: 'from Param Tuner',
  prompt_tuner: 'from Prompt Tuner',
  judge: 'from Judge',
}

const appliedFromColors = {
  param_tuner: { bg: 'rgba(99,102,241,0.1)', border: 'rgba(99,102,241,0.25)', text: '#818CF8' },
  prompt_tuner: { bg: 'rgba(168,85,247,0.1)', border: 'rgba(168,85,247,0.25)', text: '#A855F7' },
  judge: { bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.25)', text: '#F59E0B' },
}

const appliedFromLabel = computed(() => {
  return appliedFromLabels[ctx.lastUpdatedBy] || ('from ' + ctx.lastUpdatedBy)
})

const appliedFromStyle = computed(() => {
  const c = appliedFromColors[ctx.lastUpdatedBy] || {
    bg: 'rgba(255,255,255,0.04)',
    border: 'rgba(255,255,255,0.08)',
    text: '#A1A1AA',
  }
  return {
    background: c.bg,
    borderColor: c.border,
    color: c.text,
  }
})
</script>
