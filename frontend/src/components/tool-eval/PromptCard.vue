<template>
  <div
    class="rounded-md px-4 py-3 transition-colors"
    :class="{ 'bg-lime-400/[0.03]': isBest, 'bg-white/[0.01]': !isBest }"
    style="border:1px solid var(--border-subtle);"
  >
    <div class="flex items-center justify-between mb-2">
      <div class="flex items-center gap-2">
        <span class="text-xs font-mono text-zinc-500">#{{ index + 1 }}</span>
        <span v-if="prompt.style" class="text-[10px] px-1.5 py-0.5 rounded-sm font-body"
          style="background:rgba(255,255,255,0.04);border:1px solid var(--border-subtle);color:var(--zinc-500);"
        >{{ prompt.style }}</span>
        <span v-if="prompt.mutation_type" class="text-[10px] px-1.5 py-0.5 rounded-sm font-body"
          style="background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.2);color:#A855F7;"
        >{{ prompt.mutation_type }}</span>
        <span v-if="isBest" class="text-[9px] px-1.5 py-0.5 rounded-sm bg-lime-400/10 text-lime-400 font-display tracking-wider uppercase">best</span>
      </div>
      <span class="text-sm font-mono font-bold" :style="{ color: scoreColor(score * 100) }">
        {{ (score * 100).toFixed(1) }}%
      </span>
    </div>

    <!-- Prompt text -->
    <div class="mb-2">
      <div
        class="text-xs text-zinc-400 font-body rounded-sm px-3 py-2 cursor-pointer"
        style="background:rgba(0,0,0,0.2);border:1px solid var(--border-subtle);"
        :style="{ maxHeight: expanded ? 'none' : '80px', overflow: expanded ? 'visible' : 'hidden' }"
        @click="expanded = !expanded"
      >
        {{ prompt.text || '(empty)' }}
      </div>
      <button
        v-if="prompt.text && prompt.text.length > 200"
        @click="expanded = !expanded"
        class="text-[10px] text-zinc-600 hover:text-zinc-400 mt-1 font-body"
        style="background:none;border:none;cursor:pointer;"
      >{{ expanded ? 'Collapse' : 'Expand' }}</button>
    </div>

    <!-- Per-model scores -->
    <div v-if="prompt.scores && Object.keys(prompt.scores).length > 0" class="flex flex-wrap gap-2">
      <span v-for="(s, modelId) in prompt.scores" :key="modelId"
        class="text-[10px] font-mono px-1.5 py-0.5 rounded-sm"
        style="background:rgba(255,255,255,0.04);border:1px solid var(--border-subtle);"
      >
        <span class="text-zinc-500">{{ modelId.split('/').pop() }}:</span>
        <span :style="{ color: scoreColor((s.overall || 0) * 100) }"> {{ ((s.overall || 0) * 100).toFixed(0) }}%</span>
      </span>
    </div>

    <!-- Parent info -->
    <div v-if="prompt.parent_index != null" class="text-[10px] text-zinc-600 font-body mt-2">
      Mutated from parent #{{ prompt.parent_index + 1 }}
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  prompt: { type: Object, required: true },
  index: { type: Number, default: 0 },
  isBest: { type: Boolean, default: false },
})

const expanded = ref(false)

const score = computed(() => props.prompt.avg_score || 0)

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
}
</script>
