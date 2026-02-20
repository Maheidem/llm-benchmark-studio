<template>
  <div v-if="visible" class="modal-overlay" @click.self="$emit('close')">
    <div class="modal-box" style="max-width:640px;max-height:80vh;overflow-y:auto;">
      <div class="flex items-center justify-between mb-4">
        <div class="modal-title">Import from MCP Server</div>
        <button @click="$emit('close')" class="text-zinc-600 hover:text-zinc-400 text-lg">&times;</button>
      </div>

      <!-- Step 1: Connect -->
      <div v-if="step === 1">
        <div class="modal-message">Enter the URL of an MCP-compatible server to discover available tools.</div>

        <div class="mb-3">
          <input
            v-model="serverUrl"
            class="modal-input"
            placeholder="https://your-server.com/mcp"
            @keydown.enter="connect"
          >
        </div>

        <div v-if="connectError" class="text-xs mb-3" style="color:var(--coral);">{{ connectError }}</div>
        <div v-if="connecting" class="text-xs text-zinc-500 mb-3">Connecting...</div>

        <div class="modal-buttons">
          <button @click="$emit('close')" class="modal-btn modal-btn-cancel">Cancel</button>
          <button @click="connect" :disabled="connecting" class="modal-btn modal-btn-confirm">
            {{ connecting ? 'Connecting...' : 'Connect' }}
          </button>
        </div>
      </div>

      <!-- Step 2: Select Tools -->
      <div v-if="step === 2">
        <div class="text-xs text-zinc-500 mb-3 font-body">{{ serverInfo }}</div>

        <div class="mb-3">
          <label class="text-[10px] font-display tracking-wider uppercase text-zinc-500 mb-1 block">Suite Name</label>
          <input v-model="suiteName" class="modal-input" style="margin-bottom:8px;">
        </div>

        <div class="mb-3">
          <label class="text-[10px] font-display tracking-wider uppercase text-zinc-500 mb-1 block">Description</label>
          <input v-model="suiteDesc" class="modal-input" style="margin-bottom:8px;">
        </div>

        <div class="flex items-center gap-2 mb-3">
          <button @click="selectAll(true)" class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300">Select All</button>
          <button @click="selectAll(false)" class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300">Deselect All</button>
        </div>

        <div class="space-y-2 mb-4" style="max-height:300px;overflow-y:auto;">
          <label
            v-for="(tool, i) in discoveredTools"
            :key="i"
            class="flex gap-3 p-3 rounded cursor-pointer"
            style="border:1px solid var(--border-subtle);"
            :style="{ background: selectedTools[i] ? 'rgba(191,255,0,0.04)' : 'transparent' }"
          >
            <input type="checkbox" v-model="selectedTools[i]" class="mt-1 accent-lime-400">
            <div class="flex-1 min-w-0">
              <div class="text-sm text-zinc-200 font-mono">{{ tool.name }}</div>
              <div class="text-xs text-zinc-500 mt-0.5">{{ tool.description }}</div>
              <div v-if="toolParams(tool).length" class="text-xs text-zinc-600 mt-1">
                Params: <span v-for="(p, j) in toolParams(tool)" :key="j">
                  <span class="text-zinc-500">{{ p.name }}</span>
                  <span class="text-zinc-700"> ({{ p.type }}{{ p.required ? ', required' : ', optional' }})</span>
                  <span v-if="j < toolParams(tool).length - 1">, </span>
                </span>
              </div>
            </div>
          </label>
        </div>

        <label class="flex items-center gap-2 mb-4 text-xs text-zinc-500 cursor-pointer">
          <input type="checkbox" v-model="generateTests" class="accent-lime-400">
          <span>Auto-generate test cases</span>
        </label>

        <div class="modal-buttons">
          <button @click="step = 1" class="modal-btn modal-btn-cancel">Back</button>
          <button @click="doImport" :disabled="importing || selectedCount === 0" class="modal-btn modal-btn-confirm">
            {{ importing ? 'Importing...' : `Import ${selectedCount} Tool${selectedCount !== 1 ? 's' : ''}` }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { useToolEvalStore } from '../../stores/toolEval.js'
import { useToast } from '../../composables/useToast.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'imported'])

const store = useToolEvalStore()
const { showToast } = useToast()

const step = ref(1)
const serverUrl = ref('')
const connectError = ref('')
const connecting = ref(false)
const serverInfo = ref('')
const discoveredTools = ref([])
const selectedTools = reactive({})
const suiteName = ref('')
const suiteDesc = ref('')
const generateTests = ref(true)
const importing = ref(false)

const selectedCount = computed(() => {
  return Object.values(selectedTools).filter(Boolean).length
})

async function connect() {
  const url = serverUrl.value.trim()
  if (!url) {
    connectError.value = 'Enter a server URL'
    return
  }

  connectError.value = ''
  connecting.value = true

  try {
    const data = await store.mcpDiscover(url)
    discoveredTools.value = data.tools || []
    serverInfo.value = `Connected to: ${data.server_name} (${data.tool_count} tool${data.tool_count !== 1 ? 's' : ''} found)`

    // Select all by default
    discoveredTools.value.forEach((_, i) => { selectedTools[i] = true })

    // Default suite name
    const names = discoveredTools.value.slice(0, 3).map(t => t.name)
    const suffix = discoveredTools.value.length > 3 ? '...' : ''
    suiteName.value = `MCP: ${names.join(', ')}${suffix}`
    suiteDesc.value = `Imported from MCP server: ${url}`

    step.value = 2
  } catch (e) {
    connectError.value = e.message || 'Connection failed'
  } finally {
    connecting.value = false
  }
}

function selectAll(checked) {
  discoveredTools.value.forEach((_, i) => { selectedTools[i] = checked })
}

function toolParams(tool) {
  const props = (tool.inputSchema || {}).properties || {}
  const required = (tool.inputSchema || {}).required || []
  return Object.entries(props).map(([name, schema]) => ({
    name,
    type: schema.type || '?',
    required: required.includes(name),
  }))
}

async function doImport() {
  const tools = discoveredTools.value.filter((_, i) => selectedTools[i])
  if (!tools.length) {
    showToast('Select at least one tool', 'error')
    return
  }

  importing.value = true
  try {
    const data = await store.mcpImport({
      suite_name: suiteName.value.trim(),
      suite_description: suiteDesc.value.trim(),
      tools,
      generate_test_cases: generateTests.value,
    })

    showToast(
      `Imported ${data.tools_imported} tool(s)${data.test_cases_generated ? ` with ${data.test_cases_generated} test case(s)` : ''}`,
      'success'
    )
    emit('imported', data.suite_id)
    emit('close')
    reset()
  } catch (e) {
    showToast('Import failed: ' + e.message, 'error')
  } finally {
    importing.value = false
  }
}

function reset() {
  step.value = 1
  serverUrl.value = ''
  connectError.value = ''
  discoveredTools.value = []
  Object.keys(selectedTools).forEach(k => delete selectedTools[k])
  suiteName.value = ''
  suiteDesc.value = ''
}
</script>
