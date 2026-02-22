import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiFetch } from '../utils/api.js'

export const useProfilesStore = defineStore('profiles', () => {
  // --- State ---
  const profiles = ref([])
  const selectedProfile = ref(null)
  const loading = ref(false)

  // --- Getters ---
  const profilesByModel = computed(() => {
    const groups = {}
    for (const p of profiles.value) {
      if (!groups[p.model_id]) groups[p.model_id] = []
      groups[p.model_id].push(p)
    }
    return groups
  })

  // --- Actions ---

  async function fetchProfiles(modelId = null) {
    loading.value = true
    try {
      const url = modelId ? `/api/profiles/${encodeURIComponent(modelId)}` : '/api/profiles'
      const res = await apiFetch(url)
      if (!res.ok) throw new Error('Failed to fetch profiles')
      const data = await res.json()
      profiles.value = data.profiles || []
    } finally {
      loading.value = false
    }
  }

  async function fetchProfile(id) {
    const res = await apiFetch(`/api/profiles/detail/${id}`)
    if (!res.ok) throw new Error('Profile not found')
    const data = await res.json()
    selectedProfile.value = data
    return data
  }

  async function createProfile(body) {
    const res = await apiFetch('/api/profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (res.status === 409) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || 'A profile with this name already exists')
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || `Failed to create profile (${res.status})`)
    }
    return await res.json()
  }

  async function updateProfile(id, body) {
    const res = await apiFetch(`/api/profiles/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (res.status === 409) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || 'A profile with this name already exists')
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || `Failed to update profile (${res.status})`)
    }
    return await res.json()
  }

  async function deleteProfile(id) {
    const res = await apiFetch(`/api/profiles/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error('Failed to delete profile')
    profiles.value = profiles.value.filter(p => p.id !== id)
  }

  async function setDefault(id) {
    const res = await apiFetch(`/api/profiles/${id}/set-default`, { method: 'POST' })
    if (!res.ok) throw new Error('Failed to set default')
    // Update local state: mark the target as default, clear others in same model group
    const target = profiles.value.find(p => p.id === id)
    if (target) {
      for (const p of profiles.value) {
        if (p.model_id === target.model_id) {
          p.is_default = p.id === id
        }
      }
    }
  }

  async function createFromTuner(body) {
    const res = await apiFetch('/api/profiles/from-tuner', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (res.status === 409) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || 'A profile with this name already exists')
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.error || `Failed to create profile (${res.status})`)
    }
    return await res.json()
  }

  return {
    profiles,
    selectedProfile,
    loading,
    profilesByModel,
    fetchProfiles,
    fetchProfile,
    createProfile,
    updateProfile,
    deleteProfile,
    setDefault,
    createFromTuner,
  }
})
