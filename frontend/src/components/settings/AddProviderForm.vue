<template>
  <div class="card rounded-md p-5 mt-3">
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
      <div>
        <label class="field-label">Display Name</label>
        <input v-model="form.display_name" @input="deriveFields" placeholder="My Provider" class="settings-input">
      </div>
      <div>
        <label class="field-label">Provider Key <span class="text-zinc-700 normal-case">(auto-derived, editable)</span></label>
        <input v-model="form.provider_key" placeholder="auto_generated" class="settings-input">
      </div>
      <div>
        <label class="field-label">Model ID Prefix <span class="text-zinc-700 normal-case">(e.g. lm_studio)</span></label>
        <input v-model="form.model_id_prefix" placeholder="Optional -- prepended to model IDs" class="settings-input">
      </div>
      <div>
        <label class="field-label">API Base</label>
        <input v-model="form.api_base" placeholder="https://api.example.com/v1" class="settings-input">
      </div>
      <div>
        <label class="field-label">API Key Name <span class="text-zinc-700 normal-case">(auto-derived)</span></label>
        <input v-model="form.api_key_env" placeholder="e.g. OPENAI_API_KEY" class="settings-input">
      </div>
    </div>
    <div class="flex gap-3">
      <button @click="submit" class="lime-btn">Create Provider</button>
      <button @click="$emit('cancel')" class="text-[11px] font-display tracking-wider uppercase text-zinc-600 hover:text-zinc-400 px-3 py-1.5 transition-colors">Cancel</button>
    </div>
  </div>
</template>

<script setup>
import { reactive } from 'vue'
import { slugify } from '../../utils/helpers.js'

const emit = defineEmits(['submit', 'cancel'])

const form = reactive({
  display_name: '',
  provider_key: '',
  model_id_prefix: '',
  api_base: '',
  api_key_env: '',
})

function deriveFields() {
  const slug = slugify(form.display_name)
  form.provider_key = slug
  form.api_key_env = slug.toUpperCase().replace(/-/g, '_') + '_API_KEY'
}

function submit() {
  emit('submit', { ...form })
  // Reset
  form.display_name = ''
  form.provider_key = ''
  form.model_id_prefix = ''
  form.api_base = ''
  form.api_key_env = ''
}
</script>

<style scoped>
.settings-input {
  width: 100%;
  padding: 8px 12px;
  border-radius: 2px;
  font-size: 14px;
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
.lime-btn {
  font-size: 11px;
  font-family: 'Chakra Petch', sans-serif;
  font-weight: 500;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  padding: 6px 16px;
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
