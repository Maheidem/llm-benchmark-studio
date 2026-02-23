<template>
  <div class="card rounded-md p-5">
    <div class="flex items-center justify-between mb-4">
      <span class="section-label">{{ editing ? 'Edit Test Case' : 'New Test Case' }}</span>
      <button
        @click="$emit('cancel')"
        class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300"
      >Cancel</button>
    </div>

    <!-- Prompt -->
    <div class="mb-4">
      <label class="text-xs font-display tracking-wider uppercase text-zinc-500 mb-1 block">User Message</label>
      <textarea
        v-model="form.prompt"
        rows="3"
        class="w-full px-3 py-2 rounded-sm text-sm text-zinc-200"
        style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;font-family:'Space Mono',monospace;font-size:12px;resize:vertical;"
        placeholder="Enter the user prompt..."
      ></textarea>
    </div>

    <!-- Irrelevance Toggle — placed first so it controls what fields are shown below -->
    <div class="mb-4">
      <label class="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" v-model="form.shouldCallTool" class="accent-lime-400">
        <span class="text-xs font-display tracking-wider uppercase text-zinc-500">Model should call a tool</span>
      </label>
      <p v-if="!form.shouldCallTool" class="text-[10px] text-zinc-600 font-body mt-0.5 ml-5">
        Irrelevance test — correct answer is to NOT call any tool (abstain).
      </p>
    </div>

    <!-- Expected Tool (hidden for irrelevance cases) -->
    <div v-if="form.shouldCallTool" class="mb-4">
      <div class="flex items-center gap-3 mb-1">
        <label class="text-xs font-display tracking-wider uppercase text-zinc-500">Expected Tool</label>
        <label class="flex items-center gap-1.5 text-xs text-zinc-500 cursor-pointer">
          <input type="checkbox" v-model="form.noTool" class="accent-lime-400">
          <span class="text-[10px]">No tool expected</span>
        </label>
      </div>
      <input
        v-model="form.expectedTool"
        :disabled="form.noTool"
        class="w-full px-3 py-1.5 rounded-sm text-sm font-mono text-zinc-200"
        style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
        placeholder="tool_name (comma-separated for alternatives)"
      >
    </div>

    <!-- Expected Params (hidden for irrelevance cases) -->
    <div v-if="form.shouldCallTool" class="mb-4">
      <label class="text-xs font-display tracking-wider uppercase text-zinc-500 mb-1 block">Expected Parameters (JSON)</label>
      <textarea
        v-model="form.expectedParams"
        rows="3"
        class="w-full px-3 py-2 rounded-sm text-sm text-zinc-200"
        style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;font-family:'Space Mono',monospace;font-size:12px;resize:vertical;"
        placeholder='{"city": "Paris"}'
      ></textarea>
    </div>

    <!-- Scoring Mode (hidden for irrelevance cases) -->
    <div v-if="form.shouldCallTool" class="mb-4">
      <label class="text-xs font-display tracking-wider uppercase text-zinc-500 mb-1 block">Scoring Mode</label>
      <div class="flex items-center gap-3">
        <select
          v-model="form.scoringMode"
          class="text-xs font-mono px-3 py-1.5 rounded-sm"
          style="background:var(--surface);border:1px solid var(--border-subtle);color:#A1A1AA;outline:none;width:180px;"
        >
          <option value="exact">Exact Match</option>
          <option value="subset">Subset Match</option>
          <option value="numeric_tolerance">Numeric Tolerance</option>
          <option value="case_insensitive">Case Insensitive</option>
        </select>
        <div v-if="form.scoringMode === 'numeric_tolerance'" class="flex items-center gap-1">
          <span class="text-[10px] text-zinc-600">Epsilon:</span>
          <input
            v-model.number="form.epsilon"
            type="number"
            step="0.001"
            min="0"
            class="w-20 px-2 py-1 rounded-sm text-xs font-mono text-zinc-200"
            style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
          >
        </div>
      </div>
    </div>

    <!-- Multi-Turn Toggle -->
    <div class="mb-4">
      <label class="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" v-model="form.multiTurn" class="accent-lime-400">
        <span class="text-xs font-display tracking-wider uppercase text-zinc-500">Multi-Turn</span>
      </label>
    </div>

    <!-- Multi-Turn Settings -->
    <div v-if="form.multiTurn" class="mb-4 pl-4" style="border-left:2px solid var(--border-subtle);">
      <div class="flex gap-4 mb-3">
        <div>
          <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1 block">Max Rounds</label>
          <input
            v-model.number="form.maxRounds"
            type="number"
            min="1"
            max="20"
            class="w-16 px-2 py-1 rounded-sm text-xs font-mono text-zinc-200 text-center"
            style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
          >
        </div>
        <div>
          <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1 block">Optimal Hops</label>
          <input
            v-model.number="form.optimalHops"
            type="number"
            min="1"
            max="20"
            class="w-16 px-2 py-1 rounded-sm text-xs font-mono text-zinc-200 text-center"
            style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
          >
        </div>
      </div>
      <div class="mb-3">
        <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1 block">Valid Prerequisites (comma-separated)</label>
        <input
          v-model="form.prerequisites"
          class="w-full px-3 py-1.5 rounded-sm text-xs font-mono text-zinc-200"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
          placeholder="tool_a, tool_b"
        >
      </div>
      <div>
        <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase mb-1 block">Mock Responses (JSON)</label>
        <textarea
          v-model="form.mockResponses"
          rows="3"
          class="w-full px-3 py-2 rounded-sm text-xs text-zinc-200"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;font-family:'Space Mono',monospace;resize:vertical;"
          placeholder='{"tool_a": {"status": "ok", "data": []}}'
        ></textarea>
      </div>
    </div>

    <!-- Error -->
    <div v-if="error" class="text-xs mb-3" style="color:var(--coral);">{{ error }}</div>

    <!-- Actions -->
    <div class="flex justify-end gap-2">
      <button
        @click="$emit('cancel')"
        class="modal-btn modal-btn-cancel text-xs"
      >Cancel</button>
      <button
        @click="save"
        class="modal-btn modal-btn-confirm text-xs"
      >Save</button>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'

const props = defineProps({
  testCase: { type: Object, default: null },
  editing: { type: Boolean, default: false },
})

const emit = defineEmits(['save', 'cancel'])

const form = reactive({
  prompt: '',
  expectedTool: '',
  noTool: false,
  shouldCallTool: true,
  expectedParams: '',
  scoringMode: 'exact',
  epsilon: 0.01,
  multiTurn: false,
  maxRounds: 5,
  optimalHops: 2,
  prerequisites: '',
  mockResponses: '',
})

const error = ref('')

// Populate form when editing
watch(() => props.testCase, (tc) => {
  if (!tc) return
  form.prompt = tc.prompt || ''
  // should_call_tool defaults to true unless explicitly false
  form.shouldCallTool = tc.should_call_tool !== false
  const isNoTool = tc.expected_tool === null || tc.expected_tool === undefined
  form.noTool = isNoTool
  if (isNoTool) {
    form.expectedTool = ''
  } else if (Array.isArray(tc.expected_tool)) {
    form.expectedTool = tc.expected_tool.join(', ')
  } else {
    form.expectedTool = tc.expected_tool || ''
  }
  form.expectedParams = tc.expected_params ? JSON.stringify(tc.expected_params, null, 2) : ''
  const sc = tc.scoring_config || {}
  form.scoringMode = sc.mode || 'exact'
  form.epsilon = sc.epsilon ?? 0.01
  form.multiTurn = !!tc.multi_turn
  form.maxRounds = tc.max_rounds || 5
  form.optimalHops = tc.optimal_hops || 2
  form.prerequisites = (tc.valid_prerequisites || []).join(', ')
  form.mockResponses = tc.mock_responses ? JSON.stringify(tc.mock_responses, null, 2) : ''
}, { immediate: true })

function save() {
  error.value = ''

  const prompt = form.prompt.trim()
  if (!prompt) {
    error.value = 'Prompt is required'
    return
  }

  let expected_tool = null
  // Only validate expected tool when model should call a tool
  if (form.shouldCallTool && !form.noTool) {
    const toolVal = form.expectedTool.trim()
    if (!toolVal) {
      error.value = 'Expected tool is required (or uncheck "Model should call a tool")'
      return
    }
    if (toolVal.includes(',')) {
      expected_tool = toolVal.split(',').map(s => s.trim()).filter(Boolean)
      if (!expected_tool.length) {
        error.value = 'Enter at least one tool name'
        return
      }
    } else {
      expected_tool = toolVal
    }
  }

  let expected_params = null
  if (form.shouldCallTool) {
    const paramsStr = form.expectedParams.trim()
    if (paramsStr) {
      try {
        expected_params = JSON.parse(paramsStr)
        if (typeof expected_params !== 'object' || Array.isArray(expected_params)) {
          error.value = 'Expected params must be a JSON object'
          return
        }
      } catch (e) {
        error.value = 'Invalid JSON in expected params: ' + e.message
        return
      }
    }
  }

  const data = { prompt, expected_tool, expected_params, should_call_tool: form.shouldCallTool }

  // Scoring config (only relevant when tool is expected)
  if (form.shouldCallTool && form.scoringMode && form.scoringMode !== 'exact') {
    const sc = { mode: form.scoringMode }
    if (form.scoringMode === 'numeric_tolerance') {
      sc.epsilon = form.epsilon || 0.01
    }
    data.scoring_config = sc
  } else {
    data.scoring_config = null
  }

  // Multi-turn
  if (form.multiTurn) {
    data.multi_turn = true
    data.max_rounds = form.maxRounds || 5
    data.optimal_hops = form.optimalHops || 2
    const prereqs = form.prerequisites.trim()
    data.valid_prerequisites = prereqs ? prereqs.split(',').map(s => s.trim()).filter(Boolean) : []
    const mockStr = form.mockResponses.trim()
    if (mockStr) {
      try {
        data.mock_responses = JSON.parse(mockStr)
      } catch (e) {
        error.value = 'Invalid JSON in mock responses: ' + e.message
        return
      }
    }
  } else {
    data.multi_turn = false
  }

  emit('save', data)
}
</script>
