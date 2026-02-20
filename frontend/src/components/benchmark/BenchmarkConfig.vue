<template>
  <div class="card rounded-lg p-5 flex flex-col gap-5">
    <div class="section-label mb-1">Configuration</div>

    <!-- Max Tokens -->
    <div>
      <div class="flex items-center justify-between mb-2">
        <label class="text-xs text-zinc-500 font-body">Max Tokens</label>
        <span class="text-xs font-mono text-zinc-400">{{ store.maxTokens }}</span>
      </div>
      <input
        type="range"
        :value="store.maxTokens"
        min="64"
        max="4096"
        step="64"
        class="w-full"
        @input="store.maxTokens = parseInt($event.target.value)"
      />
    </div>

    <!-- Temperature -->
    <div>
      <div class="flex items-center justify-between mb-2">
        <label class="text-xs text-zinc-500 font-body">Temperature</label>
        <span class="text-xs font-mono text-zinc-400">{{ store.temperature.toFixed(1) }}</span>
      </div>
      <input
        type="range"
        :value="store.temperature"
        min="0"
        max="2"
        step="0.1"
        class="w-full"
        @input="store.temperature = parseFloat($event.target.value)"
      />
    </div>

    <!-- Runs -->
    <div>
      <div class="flex items-center justify-between mb-2">
        <label class="text-xs text-zinc-500 font-body">Runs per model</label>
      </div>
      <input
        type="number"
        :value="store.runs"
        min="1"
        max="10"
        class="w-20 px-3 py-1.5 text-sm font-mono rounded-sm prompt-input"
        @input="store.runs = Math.max(1, Math.min(10, parseInt($event.target.value) || 1))"
      />
    </div>

    <!-- Context Tiers -->
    <div>
      <label class="text-xs text-zinc-500 font-body block mb-2">Context Tiers</label>
      <TierChips :selected-tiers="store.contextTiers" @toggle="store.toggleTier" />
    </div>

    <!-- Prompt -->
    <div>
      <div class="flex items-center justify-between mb-2">
        <label class="text-xs text-zinc-500 font-body">Prompt</label>
        <select
          v-model="selectedTemplate"
          @change="onTemplateChange"
          class="text-xs font-mono px-3 py-1.5 rounded-sm cursor-pointer"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);color:#A1A1AA;outline:none;max-width:220px;"
        >
          <option value="">Custom</option>
          <option v-for="tpl in store.promptTemplates" :key="tpl.key" :value="tpl.key">
            {{ tpl.label }}
          </option>
        </select>
      </div>
      <textarea
        :value="store.prompt"
        rows="3"
        class="w-full px-3 py-2 rounded-sm prompt-input text-sm"
        placeholder="Enter prompt for benchmark..."
        @input="store.prompt = $event.target.value"
      ></textarea>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import TierChips from '../ui/TierChips.vue'
import { useBenchmarkStore } from '../../stores/benchmark.js'

const store = useBenchmarkStore()

const selectedTemplate = ref('')

function onTemplateChange() {
  if (selectedTemplate.value) {
    store.applyPromptTemplate(selectedTemplate.value)
  }
}
</script>
