<template>
  <div class="flex flex-col gap-3">
    <div
      v-for="(pp, name) in providerProgress"
      :key="name"
      class="provider-progress-row"
    >
      <div class="ppr-name" :style="{ color: getColor(name).text }" :title="name">
        {{ name }}
      </div>
      <div class="ppr-mid">
        <div class="ppr-status">
          <template v-if="isDone(pp) && !hasError(pp)">
            <span style="color:#4ADE80;">&#10003;</span> Complete
          </template>
          <template v-else-if="isDone(pp) && hasError(pp)">
            <span style="color:#FBBF24;">&#9888;</span> Done with errors
          </template>
          <template v-else-if="pp.status === 'running' && pp.currentModel">
            {{ pp.currentModel }}{{ tierLabel(pp) }} (run {{ pp.currentRun }}/{{ pp.totalRuns }})
          </template>
          <template v-else>
            Waiting...
          </template>
        </div>
        <div class="ppr-track">
          <div
            :class="['ppr-fill', isDone(pp) ? 'done' : '']"
            :style="{
              width: pct(pp) + '%',
              backgroundColor: barColor(pp, name),
              boxShadow: isDone(pp) ? 'none' : '0 0 8px ' + getColor(name).bar + '44',
            }"
          ></div>
        </div>
      </div>
      <div class="ppr-count">{{ pp.completedSteps }}/{{ pp.totalSteps }}</div>
    </div>

    <!-- Error details -->
    <div
      v-if="allErrors.length > 0"
      class="mt-3 p-3 rounded-sm flex flex-col gap-1.5"
      style="background:rgba(255,59,92,0.06);border:1px solid rgba(255,59,92,0.2);"
    >
      <div class="text-[11px] font-display tracking-wider uppercase" style="color:#FB7185;margin-bottom:2px;">
        Errors
      </div>
      <div
        v-for="(err, i) in allErrors"
        :key="i"
        class="text-[11px] font-body"
        style="color:#FB7185;"
      >
        {{ err.provider }} / {{ err.model }}: {{ err.message }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { getColor } from '../../utils/constants.js'

const props = defineProps({
  providerProgress: { type: Object, required: true },
})

function isDone(pp) {
  return pp.completedSteps >= pp.totalSteps
}

function hasError(pp) {
  return pp.errors.length > 0
}

function pct(pp) {
  return pp.totalSteps > 0 ? (pp.completedSteps / pp.totalSteps * 100) : 0
}

function barColor(pp, name) {
  if (hasError(pp)) return '#FBBF24'
  if (isDone(pp)) return '#4ADE80'
  return getColor(name).bar
}

function tierLabel(pp) {
  if (!pp.currentContextTokens || pp.currentContextTokens === 0) return ''
  const t = pp.currentContextTokens
  if (t >= 1000) return ` @ ${t / 1000}K ctx`
  return ` @ ${t} ctx`
}

const allErrors = computed(() => {
  const result = []
  for (const [name, pp] of Object.entries(props.providerProgress)) {
    for (const err of pp.errors) {
      result.push({ provider: name, ...err })
    }
  }
  return result
})
</script>
