<template>
  <div>
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center gap-2">
        <span class="section-label">Tools</span>
        <span class="text-xs font-mono text-zinc-600">({{ tools.length }})</span>
      </div>
      <div class="flex items-center gap-2">
        <button
          @click="$emit('importTools')"
          class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300 px-2 py-1 rounded-sm border border-zinc-800 hover:border-zinc-600 transition-colors"
        >Import JSON</button>
        <button
          v-if="tools.length > 0"
          @click="$emit('exportTools')"
          class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300 px-2 py-1 rounded-sm border border-zinc-800 hover:border-zinc-600 transition-colors"
        >Export</button>
        <button
          @click="$emit('addTool')"
          class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm transition-colors"
          style="color:var(--lime);border:1px solid rgba(191,255,0,0.2);background:rgba(191,255,0,0.06);"
        >+ Add Tool</button>
      </div>
    </div>

    <div v-if="!tools.length" class="text-zinc-600 text-xs font-body">
      No tools defined yet.
    </div>

    <div v-else class="flex flex-col gap-2">
      <template v-for="(tool, index) in tools" :key="index">
        <div
          class="flex items-center justify-between px-3 py-2 rounded-sm"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);"
        >
          <div class="flex-1 min-w-0">
            <span class="text-sm font-mono text-zinc-200">{{ tool.function?.name || 'unnamed' }}</span>
            <span class="text-xs text-zinc-600 ml-2 font-body">{{ tool.function?.description || '' }}</span>
            <div v-if="paramNames(tool).length" class="text-[10px] text-zinc-700 font-mono mt-0.5">
              params: {{ paramNames(tool).join(', ') }}
            </div>
          </div>
          <div class="flex gap-2 ml-3 flex-shrink-0">
            <button
              @click="$emit('editTool', index)"
              class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300"
            >Edit</button>
            <button
              @click="$emit('deleteTool', index)"
              class="text-[10px] font-display tracking-wider uppercase text-zinc-700 hover:text-red-400"
            >Delete</button>
          </div>
        </div>
        <!-- Inline editor slot for this tool -->
        <slot name="editor" :index="index"></slot>
      </template>
    </div>

    <!-- New tool editor (when adding, not editing existing) -->
    <slot name="add-editor"></slot>
  </div>
</template>

<script setup>
defineProps({
  tools: { type: Array, required: true },
  suiteId: { type: String, default: '' },
})

defineEmits(['addTool', 'editTool', 'deleteTool', 'importTools', 'exportTools'])

function paramNames(tool) {
  const props = tool.function?.parameters?.properties
  if (!props) return []
  return Object.keys(props)
}
</script>
