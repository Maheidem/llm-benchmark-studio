<template>
  <div>
    <div v-if="loading" class="text-zinc-600 text-sm font-body">Loading settings...</div>

    <div v-else class="space-y-6">
      <!-- Opt-in card -->
      <div class="card rounded-md p-5">
        <div class="flex items-start justify-between gap-4">
          <div class="flex-1">
            <div class="text-sm font-display font-bold text-zinc-200 mb-1">Contribute to Public Leaderboard</div>
            <p class="text-xs text-zinc-500 font-body leading-relaxed">
              When enabled, your anonymized tool-eval results (model name, accuracy scores, throughput)
              will be included in the <router-link to="/leaderboard" class="text-lime-400 hover:text-lime-300">public leaderboard</router-link>.
              No prompts, test case content, or personal data is shared.
            </p>
          </div>
          <label class="flex items-center gap-2 cursor-pointer flex-shrink-0">
            <span class="text-xs font-mono text-zinc-500">{{ optIn ? 'Enabled' : 'Disabled' }}</span>
            <div
              class="relative inline-block w-10 h-5 rounded-full transition-colors cursor-pointer"
              :style="{ background: optIn ? 'var(--lime)' : 'rgba(255,255,255,0.08)' }"
              @click="toggleOptIn"
            >
              <div
                class="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform shadow-sm"
                :style="{ transform: optIn ? 'translateX(20px)' : 'translateX(0)' }"
              ></div>
            </div>
          </label>
        </div>

        <!-- What's shared -->
        <div class="mt-4 rounded-sm px-4 py-3" style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);">
          <div class="text-[10px] font-display tracking-wider uppercase text-zinc-500 mb-2">What's included</div>
          <div class="grid grid-cols-2 gap-2 text-[11px] font-body text-zinc-400">
            <div class="flex items-center gap-1.5"><span style="color:var(--lime)">&#10003;</span> Model name &amp; provider</div>
            <div class="flex items-center gap-1.5"><span style="color:var(--lime)">&#10003;</span> Tool accuracy %</div>
            <div class="flex items-center gap-1.5"><span style="color:var(--lime)">&#10003;</span> Param accuracy %</div>
            <div class="flex items-center gap-1.5"><span style="color:var(--lime)">&#10003;</span> Throughput (tok/s)</div>
            <div class="flex items-center gap-1.5"><span style="color:var(--coral)">&#10007;</span> Test case prompts</div>
            <div class="flex items-center gap-1.5"><span style="color:var(--coral)">&#10007;</span> API keys or credentials</div>
          </div>
        </div>

        <div v-if="saving" class="mt-3 text-[10px] text-zinc-600 font-body">Saving...</div>
        <div v-if="saveSuccess" class="mt-3 text-[10px] font-body" style="color:var(--lime)">Saved!</div>
        <div v-if="saveError" class="mt-3 text-[10px] font-body" style="color:var(--coral)">{{ saveError }}</div>
      </div>

      <!-- View leaderboard link -->
      <div class="flex items-center gap-3">
        <router-link to="/leaderboard"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm inline-flex items-center gap-1.5"
          style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime);"
        >
          <span>&#9733;</span>
          View Public Leaderboard
        </router-link>
        <span class="text-[10px] text-zinc-600 font-body">No login required</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { apiFetch } from '../../utils/api.js'

const loading = ref(true)
const saving = ref(false)
const saveSuccess = ref(false)
const saveError = ref('')
const optIn = ref(false)

onMounted(async () => {
  try {
    const res = await apiFetch('/api/leaderboard/opt-in')
    if (res.ok) {
      const data = await res.json()
      optIn.value = data.opt_in ?? false
    }
  } catch {
    // Non-fatal
  } finally {
    loading.value = false
  }
})

async function toggleOptIn() {
  const newVal = !optIn.value
  optIn.value = newVal
  saving.value = true
  saveSuccess.value = false
  saveError.value = ''
  try {
    const res = await apiFetch('/api/leaderboard/opt-in', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ opt_in: newVal }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || err.error || 'Failed to save')
    }
    saveSuccess.value = true
    setTimeout(() => { saveSuccess.value = false }, 2000)
  } catch (e) {
    saveError.value = e.message || 'Failed to save'
    optIn.value = !newVal  // revert
  } finally {
    saving.value = false
  }
}
</script>
