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

    <!-- Timeout -->
    <div>
      <div class="flex items-center justify-between mb-2">
        <label class="text-xs text-zinc-500 font-body">Timeout (seconds)</label>
        <span class="text-xs font-mono text-zinc-400">{{ store.timeout }}s</span>
      </div>
      <input
        type="range"
        :value="store.timeout"
        min="30"
        max="600"
        step="30"
        class="w-full"
        @input="store.timeout = parseInt($event.target.value)"
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

    <!-- Advanced Parameters (collapsible) -->
    <div>
      <button
        @click="showAdvanced = !showAdvanced"
        class="flex items-center gap-1.5 text-xs text-zinc-500 font-body hover:text-zinc-300 transition-colors"
      >
        <svg :class="['w-3 h-3 transition-transform', showAdvanced ? 'rotate-90' : '']" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
        Advanced Parameters
        <span v-if="activeParamCount > 0" class="text-[10px] font-mono text-amber-400">({{ activeParamCount }} set)</span>
      </button>
      <div v-if="showAdvanced" class="mt-3 flex flex-col gap-4 pl-1" style="border-left:2px solid var(--border-subtle)">
        <p class="text-[10px] text-zinc-600 font-body ml-3">
          Tier 2 params — validated per provider. Unsupported params are auto-dropped.
        </p>

        <!-- top_p -->
        <div class="ml-3">
          <div class="flex items-center justify-between mb-1">
            <label class="text-xs text-zinc-500 font-body">top_p</label>
            <span class="text-xs font-mono text-zinc-400">{{ store.providerParams.top_p ?? '—' }}</span>
          </div>
          <input
            type="range"
            :value="store.providerParams.top_p ?? 1.0"
            min="0" max="1" step="0.05"
            class="w-full"
            @input="store.providerParams.top_p = parseFloat($event.target.value)"
          />
          <div class="flex justify-between mt-0.5">
            <span class="text-[9px] text-zinc-700">0</span>
            <button class="text-[9px] text-zinc-600 hover:text-zinc-400" @click="delete store.providerParams.top_p">reset</button>
            <span class="text-[9px] text-zinc-700">1</span>
          </div>
        </div>

        <!-- frequency_penalty -->
        <div class="ml-3">
          <div class="flex items-center justify-between mb-1">
            <label class="text-xs text-zinc-500 font-body">frequency_penalty</label>
            <span class="text-xs font-mono text-zinc-400">{{ store.providerParams.frequency_penalty ?? '—' }}</span>
          </div>
          <input
            type="range"
            :value="store.providerParams.frequency_penalty ?? 0"
            min="-2" max="2" step="0.1"
            class="w-full"
            @input="store.providerParams.frequency_penalty = parseFloat($event.target.value)"
          />
          <div class="flex justify-between mt-0.5">
            <span class="text-[9px] text-zinc-700">-2</span>
            <button class="text-[9px] text-zinc-600 hover:text-zinc-400" @click="delete store.providerParams.frequency_penalty">reset</button>
            <span class="text-[9px] text-zinc-700">2</span>
          </div>
        </div>

        <!-- presence_penalty -->
        <div class="ml-3">
          <div class="flex items-center justify-between mb-1">
            <label class="text-xs text-zinc-500 font-body">presence_penalty</label>
            <span class="text-xs font-mono text-zinc-400">{{ store.providerParams.presence_penalty ?? '—' }}</span>
          </div>
          <input
            type="range"
            :value="store.providerParams.presence_penalty ?? 0"
            min="-2" max="2" step="0.1"
            class="w-full"
            @input="store.providerParams.presence_penalty = parseFloat($event.target.value)"
          />
          <div class="flex justify-between mt-0.5">
            <span class="text-[9px] text-zinc-700">-2</span>
            <button class="text-[9px] text-zinc-600 hover:text-zinc-400" @click="delete store.providerParams.presence_penalty">reset</button>
            <span class="text-[9px] text-zinc-700">2</span>
          </div>
        </div>

        <!-- seed -->
        <div class="ml-3">
          <div class="flex items-center justify-between mb-1">
            <label class="text-xs text-zinc-500 font-body">seed</label>
          </div>
          <div class="flex items-center gap-2">
            <input
              type="number"
              :value="store.providerParams.seed ?? ''"
              min="0"
              class="w-28 px-3 py-1.5 text-sm font-mono rounded-sm prompt-input"
              placeholder="—"
              @input="$event.target.value ? store.providerParams.seed = parseInt($event.target.value) : delete store.providerParams.seed"
            />
            <button class="text-[9px] text-zinc-600 hover:text-zinc-400" @click="delete store.providerParams.seed">reset</button>
          </div>
        </div>

        <!-- Custom passthrough (JSON) -->
        <div class="ml-3">
          <div class="flex items-center justify-between mb-1">
            <label class="text-xs text-zinc-500 font-body">Custom passthrough (JSON)</label>
          </div>
          <textarea
            :value="passthroughText"
            rows="2"
            class="w-full px-3 py-2 rounded-sm prompt-input text-xs font-mono"
            placeholder='e.g. {"repetition_penalty": 1.2}'
            @blur="onPassthroughBlur($event.target.value)"
          ></textarea>
          <p v-if="passthroughError" class="text-[10px] text-red-400 mt-1">{{ passthroughError }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import TierChips from '../ui/TierChips.vue'
import { useBenchmarkStore } from '../../stores/benchmark.js'

const store = useBenchmarkStore()

const selectedTemplate = ref('')
const showAdvanced = ref(false)
const passthroughError = ref('')

const activeParamCount = computed(() => {
  return Object.entries(store.providerParams).filter(([k, v]) => v != null && k !== 'passthrough').length
    + (store.providerParams.passthrough && Object.keys(store.providerParams.passthrough).length > 0 ? 1 : 0)
})

const passthroughText = computed(() => {
  const pt = store.providerParams.passthrough
  return pt && Object.keys(pt).length > 0 ? JSON.stringify(pt) : ''
})

function onPassthroughBlur(val) {
  passthroughError.value = ''
  if (!val || !val.trim()) {
    delete store.providerParams.passthrough
    return
  }
  try {
    const parsed = JSON.parse(val)
    if (typeof parsed !== 'object' || Array.isArray(parsed)) {
      passthroughError.value = 'Must be a JSON object'
      return
    }
    store.providerParams.passthrough = parsed
  } catch {
    passthroughError.value = 'Invalid JSON'
  }
}

function onTemplateChange() {
  if (selectedTemplate.value) {
    store.applyPromptTemplate(selectedTemplate.value)
  }
}
</script>
