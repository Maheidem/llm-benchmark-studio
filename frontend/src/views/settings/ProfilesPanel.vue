<template>
  <div>
    <div v-if="store.loading && store.profiles.length === 0" class="text-zinc-600 text-sm font-body">
      Loading profiles...
    </div>
    <div v-else class="space-y-6">
      <!-- Header row -->
      <div class="flex items-center justify-between">
        <p class="text-[11px] text-zinc-600 font-body">
          Profiles store saved parameter sets and system prompts for a model.
        </p>
        <button
          @click="openCreate()"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-colors"
          style="color:var(--lime);border:1px solid rgba(191,255,0,0.2);"
        >
          + New Profile
        </button>
      </div>

      <!-- Empty state -->
      <div
        v-if="Object.keys(store.profilesByModel).length === 0"
        class="card rounded-md px-5 py-8 text-center text-zinc-600 text-xs font-body"
      >
        No profiles yet. Create one to save parameter sets and system prompts for a model.
      </div>

      <!-- Model groups -->
      <div
        v-for="(modelProfiles, modelId) in store.profilesByModel"
        :key="modelId"
        class="card rounded-md overflow-hidden"
      >
        <!-- Model header -->
        <div class="px-5 py-3 flex items-center justify-between" style="border-bottom:1px solid var(--border-subtle)">
          <span class="section-label truncate" :title="modelId">{{ modelId }}</span>
          <button
            @click="openCreate(modelId)"
            class="text-[9px] font-display tracking-wider uppercase px-2 py-0.5 rounded-sm transition-colors text-zinc-500 hover:text-zinc-300"
            style="border:1px solid var(--border-subtle)"
          >+ Add</button>
        </div>

        <!-- Profile rows -->
        <div class="divide-y" style="border-color:var(--border-subtle)">
          <div
            v-for="profile in modelProfiles"
            :key="profile.id"
            class="px-5 py-3 flex items-start justify-between gap-4 group"
          >
            <!-- Left: name + badges + meta -->
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-xs font-body text-zinc-200">{{ profile.name }}</span>
                <!-- Default badge -->
                <span
                  v-if="profile.is_default"
                  class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm"
                  style="background:rgba(191,255,0,0.08);color:var(--lime);border:1px solid rgba(191,255,0,0.2)"
                >DEFAULT</span>
                <!-- Origin badge -->
                <span
                  class="text-[9px] font-display tracking-wider uppercase px-1.5 py-0.5 rounded-sm"
                  :style="originBadgeStyle(profile.origin_type)"
                >{{ profile.origin_type }}</span>
              </div>
              <!-- Description -->
              <p v-if="profile.description" class="text-[10px] text-zinc-600 font-body mt-0.5 truncate">
                {{ profile.description }}
              </p>
              <!-- Params preview -->
              <p v-if="profile.params_json && Object.keys(safeParseParams(profile.params_json)).length > 0"
                class="text-[10px] font-mono text-zinc-700 mt-0.5 truncate"
              >
                {{ formatParams(profile.params_json) }}
              </p>
              <!-- Date -->
              <p class="text-[9px] text-zinc-700 font-body mt-0.5">
                {{ formatDate(profile.created_at) }}
              </p>
            </div>

            <!-- Right: actions -->
            <div class="flex items-center gap-2 shrink-0">
              <button
                v-if="!profile.is_default"
                @click="handleSetDefault(profile)"
                class="text-[10px] font-display tracking-wider uppercase text-zinc-600 hover:text-zinc-300 transition-colors"
              >Set Default</button>
              <button
                @click="openEdit(profile)"
                class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-200 transition-colors"
              >Edit</button>
              <button
                @click="handleDelete(profile)"
                class="text-[10px] font-display tracking-wider uppercase text-zinc-600 hover:text-red-400 transition-colors"
              >Delete</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Create/Edit modal -->
    <div
      v-if="modalVisible"
      class="fixed inset-0 z-50 flex items-center justify-center p-4"
      style="background:rgba(0,0,0,0.6)"
      @click.self="closeModal()"
    >
      <div
        class="w-full max-w-lg rounded-md overflow-hidden"
        style="background:var(--surface);border:1px solid var(--border-subtle)"
      >
        <!-- Modal header -->
        <div class="px-5 py-4" style="border-bottom:1px solid var(--border-subtle)">
          <span class="section-label">{{ editingProfile ? 'Edit Profile' : 'New Profile' }}</span>
        </div>

        <!-- Modal body -->
        <div class="px-5 py-4 space-y-4 max-h-[70vh] overflow-y-auto">
          <!-- Model (only for create) -->
          <div v-if="!editingProfile">
            <label class="field-label">Model *</label>
            <select v-model="form.model_id" class="settings-input">
              <option value="" disabled>Select a model...</option>
              <option v-for="m in configStore.allModels" :key="m.compoundKey" :value="m.model_id">
                {{ m.display_name || m.model_id }} ({{ m.provider }})
              </option>
            </select>
          </div>

          <!-- Name -->
          <div>
            <label class="field-label">Name *</label>
            <input
              v-model="form.name"
              type="text"
              placeholder="e.g. High Accuracy"
              class="settings-input"
            />
          </div>

          <!-- Description -->
          <div>
            <label class="field-label">Description</label>
            <input
              v-model="form.description"
              type="text"
              placeholder="Optional description"
              class="settings-input"
            />
          </div>

          <!-- System Prompt -->
          <div>
            <label class="field-label">System Prompt</label>
            <textarea
              v-model="form.system_prompt"
              rows="3"
              placeholder="Optional system prompt override"
              class="w-full px-3 py-2 rounded-sm text-xs font-mono text-zinc-200"
              style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;resize:vertical;"
            ></textarea>
          </div>

          <!-- Params editor -->
          <div>
            <div class="flex items-center justify-between mb-2">
              <label class="field-label">Parameters</label>
              <button
                @click="addParam()"
                class="text-[9px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300 transition-colors"
              >+ Add</button>
            </div>
            <div class="space-y-2">
              <div
                v-for="(pair, idx) in paramPairs"
                :key="idx"
                class="flex gap-2 items-center"
              >
                <input
                  v-model="pair.key"
                  type="text"
                  placeholder="key"
                  class="settings-input flex-1"
                />
                <input
                  v-model="pair.value"
                  type="text"
                  placeholder="value"
                  class="settings-input flex-1"
                />
                <button
                  @click="removeParam(idx)"
                  class="text-[10px] text-zinc-600 hover:text-red-400 transition-colors shrink-0"
                >x</button>
              </div>
              <p v-if="paramPairs.length === 0" class="text-[10px] text-zinc-700 font-body">
                No parameters. Click "+ Add" to add key-value pairs.
              </p>
            </div>
          </div>

          <!-- Set as default -->
          <div class="flex items-center gap-2">
            <input
              id="is-default"
              v-model="form.is_default"
              type="checkbox"
              class="accent-lime-400"
            />
            <label for="is-default" class="text-xs font-body text-zinc-400 cursor-pointer">
              Set as default for this model
            </label>
          </div>
        </div>

        <!-- Modal footer -->
        <div class="px-5 py-4 flex items-center justify-end gap-3" style="border-top:1px solid var(--border-subtle)">
          <button
            @click="closeModal()"
            class="text-[10px] font-display tracking-wider uppercase text-zinc-500 hover:text-zinc-300 px-3 py-1.5 transition-colors"
          >Cancel</button>
          <button
            @click="handleSave()"
            :disabled="saving"
            class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-colors disabled:opacity-50"
            style="color:var(--lime);border:1px solid rgba(191,255,0,0.2)"
          >{{ saving ? 'Saving...' : 'Save' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useProfilesStore } from '../../stores/profiles.js'
import { useConfigStore } from '../../stores/config.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'

const store = useProfilesStore()
const configStore = useConfigStore()
const { showToast } = useToast()
const { confirm } = useModal()

const modalVisible = ref(false)
const editingProfile = ref(null)
const saving = ref(false)

const form = reactive({
  model_id: '',
  name: '',
  description: '',
  system_prompt: '',
  is_default: false,
})

const paramPairs = ref([])

function safeParseParams(value) {
  if (!value) return {}
  if (typeof value === 'object') return value
  try { return JSON.parse(value) } catch { return {} }
}

function formatParams(value) {
  const obj = safeParseParams(value)
  return Object.entries(obj)
    .map(([k, v]) => `${k}=${v}`)
    .join(', ')
}

function formatDate(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch { return iso }
}

function originBadgeStyle(origin) {
  const styles = {
    manual: 'background:rgba(113,113,122,0.12);color:#71717A;border:1px solid rgba(113,113,122,0.2)',
    param_tuner: 'background:rgba(56,189,248,0.08);color:#38BDF8;border:1px solid rgba(56,189,248,0.2)',
    prompt_tuner: 'background:rgba(168,85,247,0.08);color:#A855F7;border:1px solid rgba(168,85,247,0.2)',
    import: 'background:rgba(251,146,60,0.08);color:#FB923C;border:1px solid rgba(251,146,60,0.2)',
  }
  return styles[origin] || styles.manual
}

function addParam() {
  paramPairs.value.push({ key: '', value: '' })
}

function removeParam(idx) {
  paramPairs.value.splice(idx, 1)
}

function openCreate(prefillModelId = '') {
  editingProfile.value = null
  form.model_id = prefillModelId
  form.name = ''
  form.description = ''
  form.system_prompt = ''
  form.is_default = false
  paramPairs.value = []
  modalVisible.value = true
}

function openEdit(profile) {
  editingProfile.value = profile
  form.name = profile.name
  form.description = profile.description || ''
  form.system_prompt = profile.system_prompt || ''
  form.is_default = !!profile.is_default
  const params = safeParseParams(profile.params_json)
  paramPairs.value = Object.entries(params).map(([key, value]) => ({ key, value: String(value) }))
  modalVisible.value = true
}

function closeModal() {
  modalVisible.value = false
  editingProfile.value = null
}

function buildParamsJson() {
  const obj = {}
  for (const pair of paramPairs.value) {
    const k = pair.key.trim()
    if (!k) continue
    const v = pair.value.trim()
    // Try to parse numbers/booleans, fall back to string
    if (v === 'true') obj[k] = true
    else if (v === 'false') obj[k] = false
    else if (v !== '' && !isNaN(Number(v))) obj[k] = Number(v)
    else obj[k] = v
  }
  return obj
}

async function handleSave() {
  if (!editingProfile.value && !form.model_id.trim()) {
    showToast('Model ID is required', 'error')
    return
  }
  if (!form.name.trim()) {
    showToast('Name is required', 'error')
    return
  }

  saving.value = true
  try {
    const params_json = buildParamsJson()
    if (editingProfile.value) {
      await store.updateProfile(editingProfile.value.id, {
        name: form.name.trim(),
        description: form.description.trim() || null,
        system_prompt: form.system_prompt.trim() || null,
        is_default: form.is_default,
        params_json,
      })
      showToast('Profile updated', 'success')
    } else {
      await store.createProfile({
        model_id: form.model_id.trim(),
        name: form.name.trim(),
        description: form.description.trim() || '',
        system_prompt: form.system_prompt.trim() || null,
        is_default: form.is_default,
        params_json,
        origin_type: 'manual',
      })
      showToast('Profile created', 'success')
    }
    closeModal()
    await store.fetchProfiles()
  } catch (err) {
    showToast(err.message, 'error')
  } finally {
    saving.value = false
  }
}

async function handleSetDefault(profile) {
  try {
    await store.setDefault(profile.id)
    showToast('Default updated', 'success')
  } catch (err) {
    showToast(err.message, 'error')
  }
}

async function handleDelete(profile) {
  const ok = await confirm(
    'Delete Profile',
    `Delete profile <strong>${profile.name}</strong>? This cannot be undone.`,
    { danger: true, confirmLabel: 'Delete' }
  )
  if (!ok) return
  try {
    await store.deleteProfile(profile.id)
    showToast('Profile deleted', 'success')
  } catch (err) {
    showToast(err.message, 'error')
  }
}

onMounted(() => {
  store.fetchProfiles()
  if (!configStore.config) configStore.loadConfig()
})
</script>

<style scoped>
.settings-input {
  width: 100%;
  padding: 7px 10px;
  border-radius: 2px;
  font-size: 12px;
  font-family: 'Space Mono', monospace;
  color: #E4E4E7;
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border-subtle);
  outline: none;
  transition: border-color 0.2s;
}
.settings-input:focus {
  border-color: rgba(191,255,0,0.3);
}
select.settings-input {
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2371717A' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  padding-right: 28px;
  cursor: pointer;
}
select.settings-input option {
  background: #18181B;
  color: #E4E4E7;
}
.field-label {
  font-size: 10px;
  color: #71717A;
  font-family: 'Chakra Petch', sans-serif;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  display: block;
  margin-bottom: 4px;
}
</style>
