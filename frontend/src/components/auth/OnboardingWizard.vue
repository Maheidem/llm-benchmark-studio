<template>
  <div
    v-if="visible"
    style="position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); z-index:10000; display:flex; align-items:center; justify-content:center;"
  >
    <div style="background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:40px; max-width:600px; width:90%; color:#e0e0e0;">

      <!-- Step 1: Choose Provider -->
      <div v-if="step === 1">
        <h2 style="color:var(--lime); margin-top:0; font-family:'Chakra Petch',sans-serif; font-size:22px; font-weight:700;">
          Welcome to Benchmark Studio!
        </h2>
        <p style="font-family:'Outfit',sans-serif; color:#A1A1AA; margin-bottom:20px;">
          Let's get you set up in 3 quick steps.
        </p>
        <h3 style="font-family:'Chakra Petch',sans-serif; font-size:14px; font-weight:600; color:#e0e0e0; text-transform:uppercase; letter-spacing:0.05em;">
          Step 1 of 3: Choose Your Provider
        </h3>
        <p style="color:#85858F; font-size:13px; font-family:'Outfit',sans-serif;">
          Pick a provider to get started. You can add more later in Settings.
        </p>

        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:16px 0;">
          <div
            v-for="prov in providers"
            :key="prov.name"
            style="border:1px solid var(--border-subtle); background:var(--surface); border-radius:8px; padding:14px; cursor:pointer; transition:all 0.15s;"
            :style="selectedProvider === prov.name ? { borderColor: 'var(--lime)', background: 'var(--lime-dim)' } : {}"
            @click="selectProvider(prov)"
          >
            <div style="font-weight:600; color:#e0e0e0; font-size:14px; font-family:'Outfit',sans-serif;">{{ prov.name }}</div>
            <div style="color:#85858F; font-size:12px; font-family:'Outfit',sans-serif; margin-top:4px;">{{ prov.description }}</div>
          </div>
        </div>

        <!-- API key input (for standard providers) -->
        <div v-if="selectedProvider && selectedProvider !== 'Custom'" style="margin:12px 0;">
          <label style="font-size:12px; color:#85858F; font-family:'Outfit',sans-serif; display:block; margin-bottom:6px;">
            {{ selectedKeyEnv }}
          </label>
          <div style="display:flex; gap:8px;">
            <input
              v-model="apiKeyValue"
              type="password"
              :placeholder="'Paste your ' + selectedProvider + ' API key'"
              style="flex:1; padding:8px 12px; border-radius:6px; border:1px solid var(--border-subtle); background:rgba(255,255,255,0.03); color:#e0e0e0; font-family:'Space Mono',monospace; font-size:13px; outline:none;"
            />
            <button
              @click="saveKey"
              style="background:var(--lime); color:#000; border:none; padding:8px 16px; border-radius:6px; cursor:pointer; font-weight:bold; font-family:'Outfit',sans-serif; font-size:13px; white-space:nowrap;"
            >
              Save Key
            </button>
          </div>
          <span
            v-if="keySaveStatus"
            style="font-size:12px; font-family:'Outfit',sans-serif; margin-top:6px; display:inline-block;"
            :style="{ color: keySaveSuccess ? 'var(--lime)' : 'var(--coral)' }"
          >
            {{ keySaveStatus }}
          </span>
        </div>

        <!-- Custom provider message -->
        <div v-if="selectedProvider === 'Custom'" style="margin:12px 0; padding:12px; border-radius:6px; border:1px solid var(--border-subtle); background:rgba(255,255,255,0.02);">
          <p style="color:#A1A1AA; font-size:13px; font-family:'Outfit',sans-serif; margin:0;">
            Custom providers can be configured in <strong style="color:#e0e0e0;">Settings</strong> after onboarding.
            You'll be able to set a provider name, API base URL, API key, and model ID prefix.
          </p>
        </div>

        <div style="display:flex; justify-content:space-between; margin-top:24px;">
          <button @click="complete" style="background:transparent; border:1px solid var(--border); color:#85858F; padding:8px 20px; border-radius:6px; cursor:pointer; font-family:'Outfit',sans-serif;">
            Skip All
          </button>
          <button @click="step = 2" style="background:var(--lime); color:#000; border:none; padding:8px 20px; border-radius:6px; cursor:pointer; font-weight:bold; font-family:'Outfit',sans-serif;">
            Next Step
          </button>
        </div>
      </div>

      <!-- Step 2: Quick Test -->
      <div v-if="step === 2">
        <h2 style="color:var(--lime); margin-top:0; font-family:'Chakra Petch',sans-serif; font-size:22px; font-weight:700;">
          Step 2 of 3: Quick Test
        </h2>
        <p style="font-family:'Outfit',sans-serif; color:#A1A1AA;">
          Want to run a quick benchmark with one model to verify your setup?
        </p>
        <p style="color:#85858F; font-size:13px; font-family:'Outfit',sans-serif;">
          This will run a single benchmark with the first available model. Takes about 10 seconds.
        </p>
        <div style="display:flex; justify-content:space-between; margin-top:24px;">
          <button @click="step = 3" style="background:transparent; border:1px solid var(--border); color:#85858F; padding:8px 20px; border-radius:6px; cursor:pointer; font-family:'Outfit',sans-serif;">
            Skip
          </button>
          <button @click="step = 3" style="background:var(--lime); color:#000; border:none; padding:8px 20px; border-radius:6px; cursor:pointer; font-weight:bold; font-family:'Outfit',sans-serif;">
            Next Step
          </button>
        </div>
      </div>

      <!-- Step 3: All Set -->
      <div v-if="step === 3">
        <h2 style="color:var(--lime); margin-top:0; font-family:'Chakra Petch',sans-serif; font-size:22px; font-weight:700;">
          You're All Set!
        </h2>
        <p style="font-family:'Outfit',sans-serif; color:#A1A1AA;">Start exploring:</p>
        <ul style="list-style:none; padding:0; font-family:'Outfit',sans-serif;">
          <li style="margin:8px 0; color:#e0e0e0;">
            <span style="color:var(--lime);">&#8594;</span> <strong>Benchmark</strong> &mdash; Run speed tests across models
          </li>
          <li style="margin:8px 0; color:#e0e0e0;">
            <span style="color:var(--lime);">&#8594;</span> <strong>Analytics</strong> &mdash; Compare models on leaderboard
          </li>
          <li style="margin:8px 0; color:#e0e0e0;">
            <span style="color:var(--lime);">&#8594;</span> <strong>Tool Eval</strong> &mdash; Test tool calling accuracy
          </li>
        </ul>
        <div style="text-align:center; margin-top:24px;">
          <button
            @click="complete"
            style="background:var(--lime); color:#000; border:none; padding:10px 32px; border-radius:6px; cursor:pointer; font-weight:bold; font-size:16px; font-family:'Outfit',sans-serif;"
          >
            Let's Go!
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { apiFetch } from '../../utils/api.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
})

const emit = defineEmits(['complete'])

const step = ref(1)
const selectedProvider = ref('')
const selectedKeyEnv = ref('')
const selectedProviderKey = ref('')
const apiKeyValue = ref('')
const keySaveStatus = ref('')
const keySaveSuccess = ref(false)

const providers = [
  { name: 'OpenAI', keyEnv: 'OPENAI_API_KEY', providerKey: 'openai', description: 'GPT models (gpt-5, gpt-5-nano)' },
  { name: 'Anthropic', keyEnv: 'ANTHROPIC_API_KEY', providerKey: 'anthropic', description: 'Claude models (Sonnet, Haiku)' },
  { name: 'Google Gemini', keyEnv: 'GEMINI_API_KEY', providerKey: 'google_gemini', description: 'Gemini models' },
  { name: 'Custom', keyEnv: '', providerKey: '', description: 'Other provider or self-hosted' },
]

function selectProvider(prov) {
  selectedProvider.value = prov.name
  selectedKeyEnv.value = prov.keyEnv
  selectedProviderKey.value = prov.providerKey
  apiKeyValue.value = ''
  keySaveStatus.value = ''
}

async function saveKey() {
  const keyVal = apiKeyValue.value.trim()
  if (!keyVal) {
    keySaveStatus.value = 'Please enter a key'
    keySaveSuccess.value = false
    return
  }
  try {
    const res = await apiFetch('/api/keys', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_key: selectedProviderKey.value, value: keyVal }),
    })
    if (res.ok) {
      keySaveStatus.value = 'Saved!'
      keySaveSuccess.value = true
    } else {
      const data = await res.json().catch(() => ({}))
      keySaveStatus.value = data.detail || 'Failed to save'
      keySaveSuccess.value = false
    }
  } catch {
    keySaveStatus.value = 'Connection error'
    keySaveSuccess.value = false
  }
}

async function complete() {
  try {
    await apiFetch('/api/onboarding/complete', { method: 'POST' })
  } catch { /* ignore */ }
  emit('complete')
}
</script>
