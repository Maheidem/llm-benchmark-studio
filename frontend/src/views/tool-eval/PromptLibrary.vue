<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Prompt Library</h2>
        <p class="text-sm text-zinc-600 font-body">Saved system prompt versions. Load into tuner or compare side-by-side.</p>
      </div>
      <button
        @click="showSaveForm = !showSaveForm"
        class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
        style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
      >+ Save New</button>
    </div>

    <!-- Save Form -->
    <div v-if="showSaveForm" class="card rounded-md p-5 mb-6">
      <div class="mb-3">
        <label class="text-xs font-display tracking-wider uppercase text-zinc-500 mb-1 block">Prompt Text</label>
        <textarea
          v-model="newPromptText"
          rows="4"
          class="w-full px-3 py-2 rounded-sm text-xs font-body text-zinc-200 resize-y"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
          placeholder="Enter system prompt..."
        ></textarea>
      </div>
      <div class="mb-3">
        <label class="text-xs font-display tracking-wider uppercase text-zinc-500 mb-1 block">Label (optional)</label>
        <input
          v-model="newLabel"
          class="w-full px-3 py-1.5 rounded-sm text-xs font-mono text-zinc-200"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
          placeholder="e.g. v1-tool-focused"
        >
      </div>
      <div v-if="saveError" class="text-xs mb-3" style="color:var(--coral);">{{ saveError }}</div>
      <div class="flex justify-end gap-2">
        <button @click="showSaveForm = false; newPromptText = ''; newLabel = ''; saveError = ''"
          class="modal-btn modal-btn-cancel text-xs">Cancel</button>
        <button @click="saveVersion" :disabled="saving"
          class="modal-btn modal-btn-confirm text-xs">
          {{ saving ? 'Saving...' : 'Save' }}
        </button>
      </div>
    </div>

    <!-- Diff Panel -->
    <div v-if="diffPair.length === 2" class="card rounded-md p-5 mb-6" style="border:1px solid rgba(56,189,248,0.2);">
      <div class="flex items-center justify-between mb-3">
        <span class="text-xs font-display tracking-wider uppercase text-zinc-400">Comparing 2 Versions</span>
        <button @click="diffPair = []" class="text-[10px] text-zinc-500 hover:text-zinc-300 font-display tracking-wider uppercase">Clear</button>
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div v-for="id in diffPair" :key="id">
          <div class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">
            {{ getVersionById(id)?.label || `Version ${getVersionById(id)?.version_number || '?'}` }}
            <span class="ml-2 text-zinc-700">{{ formatDate(getVersionById(id)?.created_at) }}</span>
          </div>
          <pre class="text-[10px] font-mono text-zinc-400 p-2 rounded-sm overflow-x-auto whitespace-pre-wrap break-all"
            style="background:rgba(0,0,0,0.25);border:1px solid var(--border-subtle);max-height:200px;overflow-y:auto;"
          >{{ getVersionById(id)?.prompt_text || '' }}</pre>
        </div>
      </div>
    </div>
    <div v-else-if="diffPair.length === 1" class="text-[10px] text-zinc-600 font-body mb-4">
      Select one more version to compare.
    </div>

    <!-- Version List -->
    <div v-if="store.loading" class="text-xs text-zinc-600 font-body text-center py-8">Loading...</div>

    <div v-else-if="store.versions.length === 0" class="text-xs text-zinc-600 font-body text-center py-8">
      No saved prompts yet. Save a version above or run a prompt tuning job.
    </div>

    <div v-else class="space-y-3">
      <div
        v-for="v in store.versions"
        :key="v.id"
        class="card rounded-md px-5 py-4"
        :style="diffPair.includes(v.id) ? 'border:1px solid rgba(56,189,248,0.3);' : ''"
      >
        <div class="flex items-start gap-3">
          <!-- Diff checkbox -->
          <button
            @click="toggleDiff(v.id)"
            class="mt-0.5 flex-shrink-0 w-4 h-4 rounded-sm border flex items-center justify-center transition-colors"
            :style="diffPair.includes(v.id)
              ? 'border-color:rgba(56,189,248,0.6);background:rgba(56,189,248,0.15);'
              : 'border-color:var(--border-subtle);background:transparent;'"
            :title="diffPair.includes(v.id) ? 'Remove from diff' : 'Add to diff'"
          >
            <svg v-if="diffPair.includes(v.id)" class="w-2.5 h-2.5" fill="none" stroke="#38BDF8" stroke-width="3" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>
            </svg>
          </button>

          <div class="flex-1 min-w-0">
            <!-- Header row -->
            <div class="flex items-center gap-2 mb-1 flex-wrap">
              <!-- Version number -->
              <span class="text-[10px] font-mono text-zinc-600">#{{ v.version_number || v.id?.slice(0, 6) }}</span>

              <!-- Label (editable) -->
              <div v-if="editingId === v.id" class="flex items-center gap-1">
                <input
                  v-model="editLabel"
                  class="px-2 py-0.5 rounded-sm text-xs font-mono text-zinc-200"
                  style="background:rgba(255,255,255,0.04);border:1px solid var(--border-subtle);outline:none;width:160px;"
                  @keyup.enter="saveLabel(v.id)"
                  @keyup.escape="editingId = null"
                >
                <button @click="saveLabel(v.id)" class="text-[10px] text-lime-400 font-display tracking-wider uppercase">Save</button>
                <button @click="editingId = null" class="text-[10px] text-zinc-500 font-display tracking-wider uppercase">Cancel</button>
              </div>
              <span
                v-else-if="v.label"
                class="text-xs font-mono text-zinc-300 cursor-pointer hover:text-zinc-100"
                @click="startEdit(v)"
                :title="'Click to edit label'"
              >{{ v.label }}</span>
              <button
                v-else
                @click="startEdit(v)"
                class="text-[10px] text-zinc-600 hover:text-zinc-400 font-display tracking-wider uppercase"
              >+ label</button>

              <!-- Source badge -->
              <span
                class="text-[10px] px-1.5 py-0.5 rounded-sm font-body"
                :style="sourceBadgeStyle(v.source)"
              >{{ v.source || 'manual' }}</span>

              <span class="text-[10px] text-zinc-700 font-body ml-auto">{{ formatDate(v.created_at) }}</span>
            </div>

            <!-- Prompt preview -->
            <div class="text-[10px] text-zinc-600 font-body truncate pr-2">
              {{ v.prompt_text?.substring(0, 120) }}{{ v.prompt_text?.length > 120 ? '...' : '' }}
            </div>
          </div>

          <!-- Actions -->
          <div class="flex items-center gap-2 flex-shrink-0">
            <button
              @click="loadIntoTuner(v)"
              class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm"
              style="background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.2);color:#A855F7;"
              title="Load into Prompt Tuner"
            >Load</button>
            <button
              @click="copyText(v.prompt_text)"
              class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm"
              style="border:1px solid var(--border-subtle);color:#71717A;"
              title="Copy to clipboard"
            >Copy</button>
            <button
              @click="deleteVersion(v)"
              class="text-[10px] text-zinc-600 hover:text-red-400 transition-colors px-1"
              title="Delete version"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { usePromptLibraryStore } from '../../stores/promptLibrary.js'
import { useSharedContext } from '../../composables/useSharedContext.js'
import { useToast } from '../../composables/useToast.js'

const router = useRouter()
const store = usePromptLibraryStore()
const { setSystemPrompt, setConfig } = useSharedContext()
const { showToast } = useToast()

// Save form
const showSaveForm = ref(false)
const newPromptText = ref('')
const newLabel = ref('')
const saveError = ref('')
const saving = ref(false)

// Inline label edit
const editingId = ref(null)
const editLabel = ref('')

// Diff comparison (holds up to 2 version IDs)
const diffPair = ref([])

onMounted(async () => {
  try {
    await store.loadVersions()
  } catch {
    showToast('Failed to load prompt library', 'error')
  }
})

async function saveVersion() {
  saveError.value = ''
  if (!newPromptText.value.trim()) {
    saveError.value = 'Prompt text is required'
    return
  }
  saving.value = true
  try {
    await store.saveVersion(newPromptText.value.trim(), newLabel.value.trim() || null)
    showToast('Prompt version saved', 'success')
    showSaveForm.value = false
    newPromptText.value = ''
    newLabel.value = ''
  } catch (e) {
    saveError.value = e.message || 'Failed to save'
  } finally {
    saving.value = false
  }
}

function startEdit(v) {
  editingId.value = v.id
  editLabel.value = v.label || ''
}

async function saveLabel(id) {
  try {
    await store.updateVersion(id, editLabel.value.trim() || null)
    editingId.value = null
    showToast('Label updated', 'success')
  } catch {
    showToast('Failed to update label', 'error')
  }
}

async function deleteVersion(v) {
  if (!confirm(`Delete this prompt version${v.label ? ` "${v.label}"` : ''}?`)) return
  try {
    await store.deleteVersion(v.id)
    diffPair.value = diffPair.value.filter(id => id !== v.id)
    showToast('Version deleted', 'success')
  } catch {
    showToast('Failed to delete version', 'error')
  }
}

function loadIntoTuner(v) {
  setSystemPrompt('_global', v.prompt_text)
  setConfig({ lastUpdatedBy: 'prompt_library' })
  showToast('Prompt loaded into tuner context', 'success')
  router.push({ name: 'PromptTunerConfig' })
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    showToast('Copied to clipboard', 'success')
  } catch {
    showToast('Failed to copy', 'error')
  }
}

function toggleDiff(id) {
  if (diffPair.value.includes(id)) {
    diffPair.value = diffPair.value.filter(x => x !== id)
  } else if (diffPair.value.length < 2) {
    diffPair.value = [...diffPair.value, id]
  } else {
    // Replace oldest selection
    diffPair.value = [diffPair.value[1], id]
  }
}

function getVersionById(id) {
  return store.versions.find(v => v.id === id)
}

function sourceBadgeStyle(source) {
  if (source === 'tuner' || source === 'prompt_tuner') {
    return 'background:rgba(168,85,247,0.1);color:#A855F7;'
  }
  return 'background:rgba(255,255,255,0.04);color:#71717A;'
}

function formatDate(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
</script>
