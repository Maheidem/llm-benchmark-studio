<template>
  <div>
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center gap-2">
        <span class="section-label">Test Cases</span>
        <span class="text-xs font-mono text-zinc-600">({{ testCases.length }})</span>
      </div>
      <button
        @click="$emit('addCase')"
        class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm transition-colors"
        style="color:var(--lime);border:1px solid rgba(191,255,0,0.2);background:rgba(191,255,0,0.06);"
      >+ Add Test Case</button>
    </div>

    <div v-if="!testCases.length" class="text-zinc-600 text-xs font-body">
      No test cases defined yet.
    </div>

    <div v-else class="flex flex-col gap-2">
      <template v-for="(c, index) in testCases" :key="c.id || index">
        <div
          class="flex items-start justify-between px-3 py-2 rounded-sm"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);"
        >
          <div class="flex-1 min-w-0">
            <div class="text-sm text-zinc-300 font-body mb-0.5">
              #{{ index + 1 }} "{{ truncate(c.prompt, 80) }}"
              <span
                v-if="c.multi_turn"
                class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm ml-2"
                style="color:var(--lime);background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.15)"
              >MULTI-TURN</span>
              <span
                v-if="c.scoring_config?.mode && c.scoring_config.mode !== 'exact'"
                class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm ml-2"
                style="color:#60A5FA;background:rgba(96,165,250,0.08);border:1px solid rgba(96,165,250,0.15)"
                :title="'Scoring: ' + c.scoring_config.mode"
              >{{ c.scoring_config.mode.replace('_', ' ').toUpperCase() }}</span>
            </div>
            <div class="text-xs font-mono text-zinc-500">
              Expected: {{ formatExpectedTool(c.expected_tool) }}
              <span v-if="c.expected_params" class="ml-1">
                ({{ formatParams(c.expected_params) }})
              </span>
            </div>
          </div>
          <div class="flex gap-2 ml-3 flex-shrink-0 mt-1">
            <button
              @click="$emit('editCase', c.id)"
              class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300"
            >Edit</button>
            <button
              @click="$emit('deleteCase', c.id)"
              class="text-[10px] font-display tracking-wider uppercase text-zinc-700 hover:text-red-400"
            >Delete</button>
          </div>
        </div>
        <!-- Inline editor slot for this test case -->
        <slot name="editor" :case-item="c" :index="index"></slot>
      </template>
    </div>

    <!-- New test case editor (when adding, not editing existing) -->
    <slot name="add-editor"></slot>
  </div>
</template>

<script setup>
defineProps({
  testCases: { type: Array, required: true },
  tools: { type: Array, default: () => [] },
})

defineEmits(['addCase', 'editCase', 'deleteCase'])

function truncate(s, max) {
  if (!s) return ''
  return s.length > max ? s.slice(0, max) + '...' : s
}

function formatExpectedTool(tool) {
  if (tool === null || tool === undefined) return '(no tool call)'
  if (Array.isArray(tool)) return tool.join(' | ')
  return tool
}

function formatParams(params) {
  if (!params || typeof params !== 'object') return ''
  return Object.entries(params)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(', ')
}
</script>
