<template>
  <div>
    <!-- Back Link -->
    <button
      @click="$router.push({ name: 'ToolEvalSuites' })"
      class="text-xs font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300 mb-4 inline-flex items-center gap-1"
    >
      <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7"/></svg>
      Back to Suites
    </button>

    <!-- Loading -->
    <div v-if="loading" class="text-center text-zinc-600 text-sm font-body py-8">Loading suite...</div>

    <template v-else-if="suite">
      <!-- Suite Name & Description -->
      <div class="card rounded-md p-5 mb-6">
        <div class="flex items-center gap-4 mb-3">
          <div class="flex-1">
            <label class="text-[10px] font-display tracking-wider uppercase text-zinc-500 mb-1 block">Suite Name</label>
            <input
              v-model="suiteName"
              @blur="saveMeta"
              class="w-full px-3 py-1.5 rounded-sm text-sm font-body text-zinc-200"
              style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
              placeholder="Suite Name"
            >
          </div>
        </div>
        <div>
          <label class="text-[10px] font-display tracking-wider uppercase text-zinc-500 mb-1 block">Description</label>
          <input
            v-model="suiteDesc"
            @blur="saveMeta"
            class="w-full px-3 py-1.5 rounded-sm text-sm font-body text-zinc-400"
            style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
            placeholder="Optional description"
          >
        </div>
      </div>

      <!-- System Prompt Indicator -->
      <div
        v-if="suite.system_prompt"
        class="flex items-center gap-3 px-4 py-2.5 rounded-md mb-6"
        style="background:rgba(168,85,247,0.06);border:1px solid rgba(168,85,247,0.2);"
      >
        <span class="text-[10px] font-display font-medium tracking-wider uppercase px-2 py-0.5 rounded-sm" style="background:rgba(168,85,247,0.15);color:#A855F7;">System Prompt</span>
        <span class="text-xs font-body text-zinc-400 flex-1 truncate">{{ truncate(suite.system_prompt, 80) }}</span>
        <button @click="clearSuiteSystemPrompt" class="text-[10px] font-display tracking-wider uppercase text-zinc-600 hover:text-red-400 flex-shrink-0">Clear</button>
      </div>

      <!-- Two Column Layout: Tools + Test Cases -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <!-- Tools Panel -->
        <div class="card rounded-md p-5">
          <ToolsList
            :tools="suite.tools || []"
            :suite-id="suiteId"
            @add-tool="showToolEditor(-1)"
            @edit-tool="showToolEditor"
            @delete-tool="confirmDeleteTool"
            @import-tools="importToolsJson"
            @export-tools="exportTools"
          >
            <!-- Inline tool editor (editing existing) -->
            <template #editor="{ index }">
              <div v-if="toolEditorVisible && editingToolIndex === index" class="mt-2 mb-2">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-[10px] font-display tracking-wider uppercase text-zinc-500">Edit Tool</span>
                  <button @click="toolEditorVisible = false" class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300">Cancel</button>
                </div>
                <textarea
                  v-model="toolJson"
                  rows="12"
                  class="w-full px-3 py-2 rounded-sm text-xs text-zinc-200"
                  style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;font-family:'Space Mono',monospace;resize:vertical;"
                ></textarea>
                <div v-if="toolJsonError" class="text-xs mt-1" style="color:var(--coral);">{{ toolJsonError }}</div>
                <div class="flex justify-end gap-2 mt-2">
                  <button @click="toolEditorVisible = false" class="modal-btn modal-btn-cancel text-xs">Cancel</button>
                  <button @click="saveToolJson" class="modal-btn modal-btn-confirm text-xs">Save Tool</button>
                </div>
              </div>
            </template>

            <!-- New tool editor (adding) -->
            <template #add-editor>
              <div v-if="toolEditorVisible && editingToolIndex < 0" class="mt-4">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-[10px] font-display tracking-wider uppercase text-zinc-500">New Tool</span>
                  <button @click="toolEditorVisible = false" class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300">Cancel</button>
                </div>
                <textarea
                  v-model="toolJson"
                  rows="12"
                  class="w-full px-3 py-2 rounded-sm text-xs text-zinc-200"
                  style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;font-family:'Space Mono',monospace;resize:vertical;"
                ></textarea>
                <div v-if="toolJsonError" class="text-xs mt-1" style="color:var(--coral);">{{ toolJsonError }}</div>
                <div class="flex justify-end gap-2 mt-2">
                  <button @click="toolEditorVisible = false" class="modal-btn modal-btn-cancel text-xs">Cancel</button>
                  <button @click="saveToolJson" class="modal-btn modal-btn-confirm text-xs">Save Tool</button>
                </div>
              </div>
            </template>
          </ToolsList>
        </div>

        <!-- Test Cases Panel -->
        <div class="card rounded-md p-5">
          <TestCasesList
            :test-cases="suite.test_cases || []"
            :tools="suite.tools || []"
            @add-case="showCaseEditor(null)"
            @edit-case="showCaseEditor"
            @delete-case="confirmDeleteCase"
          >
            <!-- Inline test case editor (editing existing) -->
            <template #editor="{ caseItem }">
              <div v-if="caseEditorVisible && editingCaseId === caseItem.id" class="mt-2 mb-2">
                <TestCaseForm
                  :test-case="editingCase"
                  :editing="true"
                  @save="saveCase"
                  @cancel="caseEditorVisible = false"
                />
              </div>
            </template>

            <!-- New test case editor (adding) -->
            <template #add-editor>
              <div v-if="caseEditorVisible && !editingCaseId" class="mt-4">
                <TestCaseForm
                  :test-case="editingCase"
                  :editing="false"
                  @save="saveCase"
                  @cancel="caseEditorVisible = false"
                />
              </div>
            </template>
          </TestCasesList>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToolEvalStore } from '../../stores/toolEval.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import ToolsList from '../../components/tool-eval/ToolsList.vue'
import TestCasesList from '../../components/tool-eval/TestCasesList.vue'
import TestCaseForm from '../../components/tool-eval/TestCaseForm.vue'

const route = useRoute()
const router = useRouter()
const store = useToolEvalStore()
const { showToast } = useToast()
const { confirm } = useModal()
const { setSuite } = useSharedContext()

const suiteId = computed(() => route.params.id)
const suite = computed(() => store.currentSuite)
const loading = ref(true)

const suiteName = ref('')
const suiteDesc = ref('')

// Tool editor state
const toolEditorVisible = ref(false)
const editingToolIndex = ref(-1)
const toolJson = ref('')
const toolJsonError = ref('')

// Test case editor state
const caseEditorVisible = ref(false)
const editingCaseId = ref(null)
const editingCase = ref(null)

onMounted(async () => {
  try {
    await store.loadSuite(suiteId.value)
    suiteName.value = suite.value?.name || ''
    suiteDesc.value = suite.value?.description || ''
    if (suite.value) {
      setSuite(suite.value.id, suite.value.name)
    }
  } catch {
    showToast('Failed to load suite', 'error')
    router.push({ name: 'ToolEvalSuites' })
  } finally {
    loading.value = false
  }
})

function truncate(s, max) {
  if (!s) return ''
  return s.length > max ? s.slice(0, max) + '...' : s
}

// --- Suite Meta ---

async function saveMeta() {
  const name = suiteName.value.trim()
  if (!name) {
    showToast('Name is required', 'error')
    return
  }
  try {
    await store.updateSuite(suiteId.value, {
      name,
      description: suiteDesc.value.trim(),
    })
    if (suite.value) {
      suite.value.name = name
      suite.value.description = suiteDesc.value.trim()
      setSuite(suite.value.id, name)
    }
  } catch {
    showToast('Failed to save', 'error')
  }
}

async function clearSuiteSystemPrompt() {
  try {
    await store.patchSuite(suiteId.value, { system_prompt: '' })
    if (suite.value) suite.value.system_prompt = null
    showToast('System prompt cleared', 'success')
  } catch {
    showToast('Failed to clear system prompt', 'error')
  }
}

// --- Tools ---

function showToolEditor(index) {
  editingToolIndex.value = index
  toolJsonError.value = ''

  if (index >= 0) {
    toolJson.value = JSON.stringify(suite.value.tools[index], null, 2)
  } else {
    toolJson.value = JSON.stringify({
      type: 'function',
      function: {
        name: 'my_tool',
        description: 'Description of what this tool does',
        parameters: {
          type: 'object',
          properties: {},
          required: []
        }
      }
    }, null, 2)
  }
  toolEditorVisible.value = true
}

async function saveToolJson() {
  toolJsonError.value = ''
  let tool
  try {
    tool = JSON.parse(toolJson.value.trim())
  } catch (e) {
    toolJsonError.value = 'Invalid JSON: ' + e.message
    return
  }
  if (!tool || typeof tool !== 'object') {
    toolJsonError.value = 'Tool must be a JSON object'
    return
  }
  if (tool.type !== 'function') {
    toolJsonError.value = 'tool.type must be "function"'
    return
  }
  if (!tool.function?.name) {
    toolJsonError.value = 'tool.function.name is required'
    return
  }

  const tools = [...(suite.value?.tools || [])]
  if (editingToolIndex.value >= 0) {
    tools[editingToolIndex.value] = tool
  } else {
    tools.push(tool)
  }

  try {
    await store.updateSuite(suiteId.value, { tools })
    suite.value.tools = tools
    toolEditorVisible.value = false
    showToast('Tool saved', 'success')
  } catch {
    showToast('Failed to save tools', 'error')
  }
}

async function confirmDeleteTool(index) {
  const fn = suite.value?.tools?.[index]?.function || {}
  const ok = await confirm('Delete Tool', `Delete "${fn.name || 'unnamed'}"?`, { danger: true, confirmLabel: 'Delete' })
  if (!ok) return

  const tools = [...(suite.value?.tools || [])]
  tools.splice(index, 1)
  try {
    await store.updateSuite(suiteId.value, { tools })
    suite.value.tools = tools
    showToast('Tool deleted', 'success')
  } catch {
    showToast('Failed to delete tool', 'error')
  }
}

function exportTools() {
  const tools = suite.value?.tools || []
  if (!tools.length) {
    showToast('No tools to export', 'error')
    return
  }
  const blob = new Blob([JSON.stringify(tools, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${(suite.value.name || 'tools').replace(/[^a-z0-9]+/gi, '_').toLowerCase()}_tools.json`
  a.click()
  URL.revokeObjectURL(a.href)
}

function importToolsJson() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.json'
  input.onchange = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    try {
      const text = await file.text()
      const tools = JSON.parse(text)
      if (!Array.isArray(tools) || !tools.length) {
        showToast('Must be a non-empty JSON array of tools', 'error')
        return
      }
      for (let i = 0; i < tools.length; i++) {
        if (tools[i].type !== 'function' || !tools[i].function?.name) {
          showToast(`Tool ${i + 1}: must have type "function" and function.name`, 'error')
          return
        }
      }
      const merged = [...(suite.value?.tools || []), ...tools]
      await store.updateSuite(suiteId.value, { tools: merged })
      suite.value.tools = merged
      showToast(`Imported ${tools.length} tool${tools.length > 1 ? 's' : ''}`, 'success')
    } catch (err) {
      showToast('Failed to import: ' + (err.message || 'invalid JSON'), 'error')
    }
  }
  input.click()
}

// --- Test Cases ---

function showCaseEditor(caseId) {
  if (caseId) {
    editingCaseId.value = caseId
    editingCase.value = (suite.value?.test_cases || []).find(c => c.id === caseId) || null
  } else {
    editingCaseId.value = null
    editingCase.value = null
  }
  caseEditorVisible.value = true
}

async function saveCase(data) {
  try {
    if (editingCaseId.value) {
      await store.updateTestCase(suiteId.value, editingCaseId.value, data)
      showToast('Test case updated', 'success')
    } else {
      await store.createTestCase(suiteId.value, data)
      showToast('Test case created', 'success')
    }
    // Reload the full suite to refresh test cases
    await store.loadSuite(suiteId.value)
    caseEditorVisible.value = false
    editingCaseId.value = null
    editingCase.value = null
  } catch (e) {
    showToast(e.message || 'Failed to save', 'error')
  }
}

async function confirmDeleteCase(caseId) {
  const ok = await confirm('Delete Test Case', 'Delete this test case? This cannot be undone.', { danger: true, confirmLabel: 'Delete' })
  if (!ok) return
  try {
    await store.deleteTestCase(suiteId.value, caseId)
    if (suite.value) {
      suite.value.test_cases = (suite.value.test_cases || []).filter(c => c.id !== caseId)
    }
    showToast('Test case deleted', 'success')
  } catch {
    showToast('Failed to delete', 'error')
  }
}
</script>
