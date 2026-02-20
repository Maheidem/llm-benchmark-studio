import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiFetch } from '../utils/api.js'

export const useConfigStore = defineStore('config', () => {
  const config = ref(null)
  const loading = ref(false)
  const providerParamsRegistry = ref(null)

  const providers = computed(() => {
    if (!config.value?.providers) return []
    return Object.entries(config.value.providers).map(([name, data]) => ({
      name,
      ...data,
      models: getProviderModels(data),
    }))
  })

  const allModels = computed(() => {
    if (!config.value?.providers) return []
    const result = []
    for (const [provider, provData] of Object.entries(config.value.providers)) {
      const models = getProviderModels(provData)
      const pk = provData.provider_key || provider
      for (const m of models) {
        result.push({
          ...m,
          provider,
          provider_key: pk,
          compoundKey: pk + '::' + m.model_id,
        })
      }
    }
    return result
  })

  function getProviderModels(provData) {
    return Array.isArray(provData) ? provData : (provData.models || [])
  }

  async function loadConfig() {
    loading.value = true
    try {
      const res = await apiFetch('/api/config')
      if (!res.ok) throw new Error('Failed to load config')
      config.value = await res.json()
    } finally {
      loading.value = false
    }
  }

  async function saveConfig(newConfig) {
    const res = await apiFetch('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newConfig),
    })
    if (!res.ok) throw new Error('Failed to save config')
    config.value = await res.json()
  }

  async function loadParamsRegistry() {
    try {
      const res = await apiFetch('/api/param-support/registry')
      if (res.ok) {
        providerParamsRegistry.value = await res.json()
      }
    } catch {
      // non-critical, ignore
    }
  }

  return {
    config,
    loading,
    providers,
    allModels,
    providerParamsRegistry,
    getProviderModels,
    loadConfig,
    saveConfig,
    loadParamsRegistry,
  }
})
