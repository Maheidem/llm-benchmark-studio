<template>
  <div>
    <div v-if="loading" class="text-zinc-600 text-sm font-body">Loading config...</div>
    <div v-else-if="error" class="text-red-400 text-xs font-body">{{ error }}</div>
    <div v-else class="space-y-4">
      <ProviderCard
        v-for="prov in providers"
        :key="prov.provider_key"
        :provider="prov"
        :discovered-models="discoveredModelsCache[prov.provider_key] || []"
        @refresh="loadConfig"
        @delete="deleteProvider"
        @deleteModel="onDeleteModel"
        @fetch="fetchModels"
        @loadDiscovery="loadDiscovery"
      />

      <!-- Add Provider -->
      <div>
        <button @click="showAddProvider = !showAddProvider" class="lime-btn">+ Add Provider</button>
        <AddProviderForm
          v-if="showAddProvider"
          @submit="addProvider"
          @cancel="showAddProvider = false"
        />
      </div>
    </div>

    <!-- Discovered Models Dialog -->
    <DiscoveredModelsDialog
      ref="discoveryDialogRef"
      :visible="discoveryDialog.visible"
      :models="discoveryDialog.models"
      :existing-model-ids="discoveryDialog.existingIds"
      @close="discoveryDialog.visible = false"
      @add="onDiscoveryAdd"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { apiFetch } from '../../utils/api.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'
import { useConfigStore } from '../../stores/config.js'
import ProviderCard from '../../components/settings/ProviderCard.vue'
import AddProviderForm from '../../components/settings/AddProviderForm.vue'
import DiscoveredModelsDialog from '../../components/settings/DiscoveredModelsDialog.vue'

const { showToast } = useToast()
const { confirm } = useModal()

const config = ref(null)
const loading = ref(true)
const error = ref('')
const showAddProvider = ref(false)
const discoveredModelsCache = ref({})
const discoveryDialogRef = ref(null)

const discoveryDialog = ref({
  visible: false,
  models: [],
  existingIds: [],
  providerKey: '',
})

const providers = computed(() => {
  if (!config.value?.providers) return []
  return Object.entries(config.value.providers).map(([displayName, data]) => ({
    display_name: displayName,
    provider_key: data.provider_key,
    models: data.models || [],
    api_base: data.api_base || '',
    api_key_env: data.api_key_env || '',
    model_id_prefix: data.model_id_prefix || '',
  }))
})

async function loadConfig() {
  try {
    const res = await apiFetch('/api/config')
    config.value = await res.json()
    // Sync Pinia config store so other tabs (Profiles) see updated models
    const configStore = useConfigStore()
    configStore.config = config.value
    error.value = ''
  } catch (e) {
    error.value = 'Failed to load config.'
  } finally {
    loading.value = false
  }
}

async function deleteProvider(prov) {
  const ok = await confirm(
    'Delete Provider',
    `Delete <strong>${prov.display_name}</strong> and its ${prov.models.length} model(s)?`,
    { danger: true, confirmLabel: 'Delete' }
  )
  if (!ok) return
  try {
    await apiFetch('/api/config/provider', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_key: prov.provider_key }),
    })
    await loadConfig()
  } catch {
    showToast('Failed to delete provider', 'error')
  }
}

async function onDeleteModel({ providerKey, model }) {
  const ok = await confirm(
    'Remove Model',
    `Remove model <strong>${model.display_name}</strong>?`,
    { danger: true, confirmLabel: 'Remove' }
  )
  if (!ok) return
  try {
    await apiFetch('/api/config/model', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_key: providerKey, model_id: model.model_id }),
    })
    await loadConfig()
  } catch {
    showToast('Failed to delete model', 'error')
  }
}

async function addProvider(form) {
  if (!form.provider_key) {
    showToast('Provider key is required', 'error')
    return
  }
  try {
    const res = await apiFetch('/api/config/provider', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    })
    if (res.ok) {
      showAddProvider.value = false
      await loadConfig()
    } else {
      const err = await res.json()
      showToast(err.error || 'Failed to add provider', 'error')
    }
  } catch {
    showToast('Network error', 'error')
  }
}

async function loadDiscovery(providerKey) {
  if (discoveredModelsCache.value[providerKey]) return
  try {
    const res = await apiFetch(`/api/models/discover?provider_key=${encodeURIComponent(providerKey)}`)
    const data = await res.json()
    if (data.models) {
      discoveredModelsCache.value[providerKey] = data.models
    }
  } catch { /* silent */ }
}

async function fetchModels(providerKey) {
  try {
    const res = await apiFetch(`/api/models/discover?provider_key=${encodeURIComponent(providerKey)}`)
    const data = await res.json()
    if (data.error) {
      showToast(data.error, 'error')
      return
    }
    const models = data.models || []
    if (models.length === 0) {
      showToast('No models found', 'error')
      return
    }
    // Cache
    discoveredModelsCache.value[providerKey] = models
    // Get existing model IDs
    const prov = providers.value.find(p => p.provider_key === providerKey)
    const existingIds = (prov?.models || []).map(m => m.model_id)
    // Show dialog
    discoveryDialog.value = {
      visible: true,
      models,
      existingIds,
      providerKey,
    }
    discoveryDialogRef.value?.reset?.()
  } catch (e) {
    showToast('Failed to fetch models: ' + e.message, 'error')
  }
}

async function onDiscoveryAdd(models) {
  const pk = discoveryDialog.value.providerKey
  for (const m of models) {
    await apiFetch('/api/config/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_key: pk, id: m.id, display_name: m.display_name }),
    })
  }
  discoveryDialog.value.visible = false
  await loadConfig()
  showToast(`Added ${models.length} model(s)`, 'success')
}

onMounted(loadConfig)
</script>

<style scoped>
.lime-btn {
  font-size: 11px;
  font-family: 'Chakra Petch', sans-serif;
  font-weight: 500;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  padding: 8px 16px;
  border-radius: 2px;
  color: var(--lime);
  border: 1px solid rgba(191,255,0,0.2);
  background: transparent;
  cursor: pointer;
  transition: border-color 0.15s;
}
.lime-btn:hover {
  border-color: rgba(191,255,0,0.5);
}
</style>
