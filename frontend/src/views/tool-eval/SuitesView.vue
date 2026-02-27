<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Tool Calling Evaluation</h2>
        <p class="text-sm text-zinc-600 font-body">Define tool suites, create test cases, and evaluate model accuracy.</p>
      </div>
      <div class="flex items-center gap-2 flex-wrap">
        <button @click="newSuite" class="run-btn px-4 py-2 rounded-sm text-xs whitespace-nowrap flex items-center gap-2">
          <svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
          New Suite
        </button>
        <button
          @click="smartImport"
          class="px-4 py-2 rounded-sm text-xs font-display whitespace-nowrap flex items-center gap-2 border border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:border-zinc-500 transition-colors"
        >
          <svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
          Import
        </button>
        <button
          @click="downloadExample"
          class="px-4 py-2 rounded-sm text-xs font-display whitespace-nowrap flex items-center gap-2 border border-zinc-700 text-zinc-500 hover:text-zinc-300 hover:border-zinc-500 transition-colors"
          title="Download example JSON template for import"
        >
          <svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
          Example JSON
        </button>
        <button
          @click="showMcpModal = true"
          class="px-4 py-2 rounded-sm text-xs font-display whitespace-nowrap flex items-center gap-2 border border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:border-zinc-500 transition-colors"
        >
          <svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
          Import from MCP
        </button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="text-center text-zinc-600 text-sm font-body py-8">Loading suites...</div>

    <!-- Suite Table -->
    <SuiteTable
      v-else
      :suites="store.suites"
      @select="openSuite"
      @export="exportSuite"
      @delete="confirmDeleteSuite"
    />

    <!-- MCP Import Modal -->
    <McpImportModal
      :visible="showMcpModal"
      @close="showMcpModal = false"
      @imported="onMcpImported"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useToolEvalStore } from '../../stores/toolEval.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import SuiteTable from '../../components/tool-eval/SuiteTable.vue'
import McpImportModal from '../../components/tool-eval/McpImportModal.vue'

const store = useToolEvalStore()
const router = useRouter()
const { showToast } = useToast()
const { confirm } = useModal()
const { setSuite } = useSharedContext()

const loading = ref(true)
const showMcpModal = ref(false)

onMounted(async () => {
  try {
    await store.loadSuites()
  } catch {
    showToast('Failed to load suites', 'error')
  } finally {
    loading.value = false
  }
})

async function newSuite() {
  try {
    const result = await store.createSuite({ name: 'New Suite', description: '', tools: [] })
    showToast('Suite created', 'success')
    openSuite(result.suite_id)
  } catch (e) {
    showToast(e.message || 'Failed to create suite', 'error')
  }
}

function openSuite(id) {
  // Find suite name for context
  const suite = store.suites.find(s => s.id === id)
  if (suite) setSuite(id, suite.name)
  router.push({ name: 'ToolEvalEditor', params: { id } })
}

async function exportSuite(id) {
  try {
    await store.exportSuite(id)
    showToast('Suite exported', 'success')
  } catch {
    showToast('Failed to export suite', 'error')
  }
}

async function confirmDeleteSuite(id, name) {
  const ok = await confirm('Delete Suite', `Delete "${name}" and all its test cases? This cannot be undone.`, { danger: true, confirmLabel: 'Delete' })
  if (!ok) return
  try {
    await store.deleteSuite(id)
    showToast('Suite deleted', 'success')
  } catch {
    showToast('Failed to delete', 'error')
  }
}

function smartImport() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.json'
  input.onchange = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    try {
      const text = await file.text()
      const data = JSON.parse(text)
      const result = await store.smartImport(data, file.name)
      showToast(`Imported suite with ${result.test_cases_created} test cases`, 'success')
      await store.loadSuites()
      openSuite(result.suite_id)
    } catch (err) {
      showToast('Failed to import: ' + (err.message || 'invalid JSON'), 'error')
    }
  }
  input.click()
}

async function downloadExample() {
  try {
    await store.downloadImportExample()
  } catch {
    showToast('Failed to download example', 'error')
  }
}

async function onMcpImported(suiteId) {
  await store.loadSuites()
  openSuite(suiteId)
}
</script>
