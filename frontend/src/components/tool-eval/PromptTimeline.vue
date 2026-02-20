<template>
  <div class="card rounded-md p-5 mb-6">
    <div class="flex items-center justify-between mb-4">
      <span class="section-label">Generation Timeline</span>
      <span class="text-xs font-mono text-zinc-600">{{ generations.length }} generation{{ generations.length !== 1 ? 's' : '' }}</span>
    </div>

    <div v-if="generations.length === 0" class="text-xs text-zinc-600 font-body text-center py-4">
      No generations yet
    </div>

    <div v-else class="relative pl-6">
      <!-- Vertical line -->
      <div class="absolute left-2 top-2 bottom-2 w-px bg-zinc-700"></div>

      <div v-for="(gen, i) in generations" :key="i" class="relative mb-6 last:mb-0">
        <!-- Node dot -->
        <div
          class="absolute -left-4 top-1 w-3 h-3 rounded-full border-2"
          :class="isCurrentGen(gen) ? 'bg-lime-400 border-lime-400' : 'bg-zinc-800 border-zinc-600'"
        ></div>

        <!-- Content -->
        <div
          class="rounded-md px-4 py-3 cursor-pointer transition-colors"
          :class="selectedGen === i ? 'bg-white/[0.04]' : 'hover:bg-white/[0.02]'"
          style="border:1px solid var(--border-subtle);"
          @click="selectedGen = selectedGen === i ? null : i"
        >
          <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-display font-bold text-zinc-200">Gen {{ gen.generation }}</span>
            <span class="text-xs font-mono font-bold" :style="{ color: scoreColor((gen.best_score || 0) * 100) }">
              {{ ((gen.best_score || 0) * 100).toFixed(1) }}%
            </span>
          </div>

          <div v-if="gen.prompts && gen.prompts.length > 0" class="text-[10px] text-zinc-600 font-body">
            {{ gen.prompts.length }} prompts, best #{{ (gen.best_index ?? gen.best_prompt_index ?? 0) + 1 }}
          </div>

          <div v-if="gen.survivors && gen.survivors.length > 0" class="text-[10px] text-zinc-600 font-body mt-1">
            {{ gen.survivors.length }} survivor{{ gen.survivors.length !== 1 ? 's' : '' }} selected
          </div>

          <!-- Expanded prompt details -->
          <div v-if="selectedGen === i && gen.prompts" class="mt-3 space-y-2">
            <div
              v-for="(p, pi) in gen.prompts"
              :key="pi"
              class="rounded-sm px-3 py-2"
              :class="p.survived ? 'bg-lime-400/[0.03]' : 'bg-white/[0.01]'"
              style="border:1px solid var(--border-subtle);"
            >
              <div class="flex items-center justify-between mb-1">
                <div class="flex items-center gap-2">
                  <span class="text-[10px] font-mono text-zinc-500">#{{ pi + 1 }}</span>
                  <span v-if="p.style" class="text-[10px] font-body text-zinc-600">{{ p.style }}</span>
                  <span v-if="p.survived" class="text-[9px] px-1.5 py-0.5 rounded-sm bg-lime-400/10 text-lime-400 font-display tracking-wider uppercase">survivor</span>
                </div>
                <span class="text-xs font-mono font-bold" :style="{ color: scoreColor((p.avg_score || 0) * 100) }">
                  {{ ((p.avg_score || 0) * 100).toFixed(1) }}%
                </span>
              </div>
              <div v-if="p.text" class="text-xs text-zinc-400 font-body mt-1" style="max-height:60px;overflow:hidden;">
                {{ p.text.substring(0, 200) }}{{ p.text.length > 200 ? '...' : '' }}
              </div>
              <!-- Per-model scores -->
              <div v-if="p.scores && Object.keys(p.scores).length > 0" class="flex flex-wrap gap-2 mt-2">
                <span v-for="(score, modelId) in p.scores" :key="modelId"
                  class="text-[10px] font-mono px-1.5 py-0.5 rounded-sm"
                  style="background:rgba(255,255,255,0.04);border:1px solid var(--border-subtle);"
                >
                  {{ modelId.split('/').pop() }}: {{ ((score.overall || 0) * 100).toFixed(0) }}%
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Score trend -->
    <div v-if="generations.length > 1" class="mt-4 pt-3" style="border-top:1px solid var(--border-subtle);">
      <div class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-2">Score Trend</div>
      <div class="flex items-end gap-1 h-12">
        <div
          v-for="(gen, i) in generations"
          :key="i"
          class="flex-1 rounded-t-sm transition-all"
          :style="{
            height: ((gen.best_score || 0) / maxScore * 100) + '%',
            background: isCurrentGen(gen) ? 'var(--lime)' : 'rgba(191,255,0,0.3)',
            minHeight: '2px',
          }"
          :title="'Gen ' + gen.generation + ': ' + ((gen.best_score || 0) * 100).toFixed(1) + '%'"
        ></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  generations: { type: Array, default: () => [] },
  currentGenerationNum: { type: Number, default: 0 },
})

const selectedGen = ref(null)

const maxScore = computed(() => {
  if (props.generations.length === 0) return 1
  return Math.max(...props.generations.map(g => g.best_score || 0), 0.01)
})

function isCurrentGen(gen) {
  return gen.generation === props.currentGenerationNum
}

function scoreColor(pct) {
  if (pct >= 80) return 'var(--lime)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral)'
}
</script>
