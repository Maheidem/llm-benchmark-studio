import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiFetch } from '../utils/api.js'

export const usePromptLibraryStore = defineStore('promptLibrary', () => {
  // --- State ---
  const versions = ref([])
  const loading = ref(false)

  // --- Actions ---

  async function loadVersions() {
    loading.value = true
    try {
      const res = await apiFetch('/api/prompt-versions')
      if (!res.ok) throw new Error('Failed to load prompt versions')
      const data = await res.json()
      versions.value = data.versions || []
    } finally {
      loading.value = false
    }
  }

  async function saveVersion(promptText, label = null, source = 'manual', parentVersionId = null) {
    const res = await apiFetch('/api/prompt-versions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt_text: promptText,
        label: label || null,
        source,
        parent_version_id: parentVersionId || null,
      }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || 'Failed to save version')
    }
    const data = await res.json()
    await loadVersions()
    return data
  }

  async function updateVersion(id, label) {
    const res = await apiFetch(`/api/prompt-versions/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    })
    if (!res.ok) throw new Error('Failed to update version')
    const data = await res.json()
    // PATCH returns {"status":"ok"}, not the full version — update label in-place
    const idx = versions.value.findIndex(v => v.id === id)
    if (idx !== -1) versions.value[idx] = { ...versions.value[idx], label }
    return data
  }

  async function deleteVersion(id) {
    const res = await apiFetch(`/api/prompt-versions/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error('Failed to delete version')
    versions.value = versions.value.filter(v => v.id !== id)
  }

  function reset() {
    versions.value = []
    loading.value = false
  }

  return {
    // State
    versions,
    loading,

    // Actions
    loadVersions,
    saveVersion,
    updateVersion,
    deleteVersion,
    reset,
  }
})
