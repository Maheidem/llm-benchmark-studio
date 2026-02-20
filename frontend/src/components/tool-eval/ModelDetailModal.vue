<template>
  <div v-if="visible" class="modal-overlay" @click.self="$emit('close')">
    <div class="modal-box" style="max-width:720px;max-height:80vh;overflow-y:auto;">
      <div class="flex items-center justify-between mb-4">
        <div class="modal-title">{{ modelName }}</div>
        <button @click="$emit('close')" class="text-zinc-600 hover:text-zinc-400 text-lg">&times;</button>
      </div>

      <div class="space-y-3">
        <div
          v-for="(r, i) in caseResults"
          :key="r.test_case_id || i"
          class="rounded-sm p-3"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);"
        >
          <!-- Header -->
          <div class="flex items-center gap-2 mb-1">
            <span :style="{ color: r.tool_selection_score === 1.0 ? 'var(--lime)' : 'var(--coral)' }" class="text-xs font-mono font-bold">
              {{ r.tool_selection_score === 1.0 ? 'OK' : 'X' }}
            </span>
            <span class="text-xs font-mono font-bold" :style="{ color: scoreColor(r.overall_score * 100) }">
              {{ (r.overall_score * 100).toFixed(0) }}%
            </span>
            <span v-if="r.latency_ms" class="text-[10px] text-zinc-700 font-mono ml-auto">{{ r.latency_ms }}ms</span>
          </div>

          <!-- Prompt -->
          <div class="text-sm text-zinc-300 font-body mb-1">"{{ r.prompt || '' }}"</div>

          <!-- Expected vs Actual -->
          <div class="grid grid-cols-2 gap-4 text-xs font-mono">
            <div>
              <div class="text-[10px] font-display tracking-wider text-zinc-500 uppercase mb-0.5">Expected</div>
              <div class="text-zinc-400">{{ formatTool(r.expected_tool) }}</div>
              <div v-if="r.expected_params" class="text-zinc-600 mt-0.5 break-all">{{ JSON.stringify(r.expected_params) }}</div>
            </div>
            <div>
              <div class="text-[10px] font-display tracking-wider text-zinc-500 uppercase mb-0.5">Actual</div>
              <div :style="{ color: r.tool_selection_score > 0 ? 'var(--lime)' : 'var(--coral)' }">
                {{ r.actual_tool || '(no tool call)' }}
              </div>
              <div v-if="r.actual_params" class="text-zinc-600 mt-0.5 break-all">{{ JSON.stringify(r.actual_params) }}</div>
            </div>
          </div>

          <!-- Scores -->
          <div class="flex gap-4 mt-2 text-[10px] font-mono text-zinc-600">
            <span>Tool: <span :style="{ color: scoreColor(r.tool_selection_score * 100) }">{{ (r.tool_selection_score * 100).toFixed(0) }}%</span></span>
            <span v-if="r.param_accuracy != null">Param: <span :style="{ color: scoreColor(r.param_accuracy * 100) }">{{ (r.param_accuracy * 100).toFixed(0) }}%</span></span>
          </div>

          <!-- Multi-turn chain -->
          <div v-if="r.multi_turn && r.tool_chain?.length" class="mt-2">
            <div class="text-[10px] font-mono">
              <span class="text-zinc-600">Chain:</span>
              <span style="color:var(--lime)">{{ r.tool_chain.map(c => c.tool_name).join(' \u2192 ') }}</span>
              <span class="text-zinc-600 ml-1">({{ r.rounds_used || r.tool_chain.length }} round{{ (r.rounds_used || r.tool_chain.length) > 1 ? 's' : '' }})</span>
            </div>
            <div class="mt-1 grid grid-cols-4 gap-2 text-[10px] font-mono">
              <span class="text-zinc-500">Completion: <span style="color:var(--lime)">{{ ((r.completion_score || 0) * 100).toFixed(0) }}%</span></span>
              <span class="text-zinc-500">Efficiency: <span style="color:var(--lime)">{{ ((r.efficiency_score || 0) * 100).toFixed(0) }}%</span></span>
              <span class="text-zinc-500">Redundancy: <span style="color:var(--coral)">-{{ ((r.redundancy_penalty || 0) * 100).toFixed(0) }}%</span></span>
              <span class="text-zinc-500">Detour: <span style="color:var(--coral)">-{{ ((r.detour_penalty || 0) * 100).toFixed(0) }}%</span></span>
            </div>
          </div>

          <!-- Error -->
          <div v-if="r.error" class="mt-2 text-xs" style="color:var(--coral);">{{ r.error }}</div>

          <!-- Raw Toggle -->
          <div v-if="r.raw_request || r.raw_response" class="mt-2">
            <button
              @click="toggleRaw(i)"
              class="text-[10px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm"
              style="color:var(--lime);border:1px solid rgba(191,255,0,0.15)"
            >{{ rawVisible[i] ? 'HIDE RAW' : 'VIEW RAW' }}</button>
            <div v-if="rawVisible[i]" class="mt-2 space-y-2" style="max-height:300px;overflow-y:auto;">
              <div v-if="r.raw_request">
                <div class="text-[10px] font-display tracking-wider text-zinc-500 uppercase mb-1">Request</div>
                <pre class="text-[10px] font-mono text-zinc-400 p-2 rounded-sm overflow-x-auto" style="background:rgba(0,0,0,0.3);border:1px solid var(--border-subtle);white-space:pre-wrap;word-break:break-all">{{ JSON.stringify(r.raw_request, null, 2) }}</pre>
              </div>
              <div v-if="r.raw_response">
                <div class="text-[10px] font-display tracking-wider text-zinc-500 uppercase mb-1">Response</div>
                <pre class="text-[10px] font-mono text-zinc-400 p-2 rounded-sm overflow-x-auto" style="background:rgba(0,0,0,0.3);border:1px solid var(--border-subtle);white-space:pre-wrap;word-break:break-all">{{ JSON.stringify(r.raw_response, null, 2) }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="flex justify-end mt-4">
        <button @click="$emit('close')" class="modal-btn modal-btn-cancel">Close</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  modelId: { type: String, default: '' },
  allResults: { type: Array, default: () => [] },
})

defineEmits(['close'])

const rawVisible = ref({})

const caseResults = computed(() => {
  return props.allResults.filter(r => r.model_id === props.modelId)
})

const modelName = computed(() => {
  const first = caseResults.value[0]
  return first?.model_name || first?.model_id || props.modelId
})

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
}

function formatTool(tool) {
  if (tool === null || tool === undefined) return '(no tool call)'
  if (Array.isArray(tool)) return tool.join(' | ')
  return tool
}

function toggleRaw(index) {
  rawVisible.value[index] = !rawVisible.value[index]
}
</script>
