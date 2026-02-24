<template>
  <div class="min-h-screen" style="background:var(--bg,#09090B);color:#E4E4E7;">
    <!-- Top bar -->
    <div class="border-b px-6 py-4" style="border-color:var(--border-subtle,#1f1f23);">
      <div class="max-w-6xl mx-auto flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-sm flex items-center justify-center font-display font-bold text-sm" style="background:var(--lime,#BFFF00);color:#09090B;">
            B<span style="font-size:10px;opacity:0.6;">s</span>
          </div>
          <span class="font-display font-bold text-sm text-zinc-100 tracking-wide">
            BENCHMARK <span style="color:var(--lime,#BFFF00)">STUDIO</span>
          </span>
          <span class="text-zinc-600 text-xs font-body ml-2">Public Leaderboard</span>
        </div>
        <router-link v-if="authStore.isLoggedIn" to="/benchmark"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
          style="border:1px solid var(--border-subtle);color:#A1A1AA;"
        >Dashboard</router-link>
        <router-link v-else to="/login"
          class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
          style="background:rgba(191,255,0,0.08);border:1px solid rgba(191,255,0,0.2);color:var(--lime,#BFFF00);"
        >Sign In</router-link>
      </div>
    </div>

    <div class="max-w-6xl mx-auto px-4 py-8">
      <!-- Header -->
      <div class="mb-6">
        <h1 class="font-display font-bold text-2xl text-zinc-100 mb-1">Tool-Calling Leaderboard</h1>
        <p class="text-sm text-zinc-600 font-body">Community benchmark results from opted-in users. Ranks models by tool-calling accuracy.</p>
      </div>

      <!-- Controls -->
      <div class="flex items-center gap-3 mb-5 flex-wrap">
        <!-- Search -->
        <input
          v-model="searchQuery"
          class="text-xs font-mono px-3 py-1.5 rounded-sm"
          style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle,#1f1f23);color:#E4E4E7;outline:none;min-width:200px;"
          placeholder="Search model or provider..."
        >

        <!-- Provider filter -->
        <select
          v-model="filterProvider"
          class="text-xs font-mono px-3 py-1.5 rounded-sm"
          style="background:var(--surface,#111113);border:1px solid var(--border-subtle,#1f1f23);color:#A1A1AA;outline:none;"
        >
          <option value="">All providers</option>
          <option v-for="p in allProviders" :key="p" :value="p">{{ p }}</option>
        </select>

        <!-- Last updated -->
        <span v-if="lastUpdated" class="text-[10px] text-zinc-600 font-body ml-auto">
          Updated {{ lastUpdated }}
        </span>
      </div>

      <!-- Loading / empty -->
      <div v-if="loading" class="text-xs text-zinc-600 font-body text-center py-16">Loading leaderboard...</div>
      <div v-else-if="filteredData.length === 0" class="text-xs text-zinc-600 font-body text-center py-16">
        No results found.
        <span v-if="!authStore.isLoggedIn">
          <router-link to="/login" class="text-lime-400 hover:text-lime-300 ml-1">Sign in to contribute your data.</router-link>
        </span>
      </div>

      <!-- Leaderboard table -->
      <div v-else class="card rounded-md overflow-hidden" style="background:rgba(255,255,255,0.01);border:1px solid var(--border-subtle,#1f1f23);">
        <table class="w-full text-sm">
          <thead>
            <tr style="border-bottom:1px solid var(--border-subtle,#1f1f23);">
              <th class="px-4 py-3 text-left text-[10px] font-display tracking-wider uppercase text-zinc-500 w-10">#</th>
              <th class="px-4 py-3 text-left text-[10px] font-display tracking-wider uppercase text-zinc-500 cursor-pointer" @click="toggleSort('model_name')">
                Model {{ sortIndicator('model_name') }}
              </th>
              <th class="px-3 py-3 text-left text-[10px] font-display tracking-wider uppercase text-zinc-500">Provider</th>
              <th class="px-3 py-3 text-right text-[10px] font-display tracking-wider uppercase text-zinc-500 cursor-pointer" @click="toggleSort('tool_accuracy_pct')"
                title="Tool selection accuracy">
                Tool % {{ sortIndicator('tool_accuracy_pct') }}
              </th>
              <th class="px-3 py-3 text-right text-[10px] font-display tracking-wider uppercase text-zinc-500 cursor-pointer" @click="toggleSort('param_accuracy_pct')"
                title="Parameter accuracy">
                Param % {{ sortIndicator('param_accuracy_pct') }}
              </th>
              <th class="px-3 py-3 text-right text-[10px] font-display tracking-wider uppercase text-zinc-500 cursor-pointer" @click="toggleSort('irrelevance_pct')"
                title="Irrelevance detection accuracy (opt-out rate)">
                Irrel. % {{ sortIndicator('irrelevance_pct') }}
              </th>
              <th class="px-3 py-3 text-right text-[10px] font-display tracking-wider uppercase text-zinc-500 cursor-pointer" @click="toggleSort('avg_tps')"
                title="Average tokens per second">
                Tok/s {{ sortIndicator('avg_tps') }}
              </th>
              <th class="px-3 py-3 text-right text-[10px] font-display tracking-wider uppercase text-zinc-500 cursor-pointer" @click="toggleSort('avg_ttft_ms')"
                title="Average time to first token (ms)">
                TTFT {{ sortIndicator('avg_ttft_ms') }}
              </th>
              <th class="px-3 py-3 text-right text-[10px] font-display tracking-wider uppercase text-zinc-500">Samples</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(row, i) in sortedData"
              :key="row.model_id || i"
              class="transition-colors hover:bg-white/[0.015]"
              style="border-bottom:1px solid var(--border-subtle,#1f1f23);"
            >
              <td class="px-4 py-3 text-xs font-mono text-zinc-600">{{ i + 1 }}</td>
              <td class="px-4 py-3">
                <div class="text-sm font-body text-zinc-200">{{ row.model_name || row.model_id }}</div>
              </td>
              <td class="px-3 py-3">
                <span class="text-[10px] font-mono px-1.5 py-0.5 rounded-sm"
                  :style="providerStyle(row.provider)"
                >{{ row.provider || '-' }}</span>
              </td>
              <td class="px-3 py-3 text-right">
                <div class="flex items-center justify-end gap-2">
                  <div class="w-16 h-1.5 rounded-full overflow-hidden" style="background:rgba(255,255,255,0.06);">
                    <div class="h-full rounded-full" :style="{ width: (row.tool_accuracy_pct || 0) + '%', background: scoreGradient(row.tool_accuracy_pct) }"></div>
                  </div>
                  <span class="text-xs font-mono font-bold" :style="{ color: scoreColor(row.tool_accuracy_pct) }">
                    {{ row.tool_accuracy_pct != null ? row.tool_accuracy_pct.toFixed(1) + '%' : '-' }}
                  </span>
                </div>
              </td>
              <td class="px-3 py-3 text-right text-xs font-mono" :style="{ color: row.param_accuracy_pct != null ? scoreColor(row.param_accuracy_pct) : '#52525B' }">
                {{ row.param_accuracy_pct != null ? row.param_accuracy_pct.toFixed(1) + '%' : '-' }}
              </td>
              <td class="px-3 py-3 text-right text-xs font-mono" :style="{ color: row.irrelevance_pct != null ? scoreColor(row.irrelevance_pct) : '#52525B' }">
                {{ row.irrelevance_pct != null ? row.irrelevance_pct.toFixed(1) + '%' : '-' }}
              </td>
              <td class="px-3 py-3 text-right text-xs font-mono text-zinc-400">
                {{ row.avg_tps != null ? row.avg_tps.toFixed(0) : '-' }}
              </td>
              <td class="px-3 py-3 text-right text-xs font-mono text-zinc-400">
                {{ row.avg_ttft_ms != null ? row.avg_ttft_ms.toFixed(0) + 'ms' : '-' }}
              </td>
              <td class="px-3 py-3 text-right text-xs font-mono text-zinc-600">{{ row.sample_count || 0 }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Opt-in note for logged-in users -->
      <div v-if="authStore.isLoggedIn" class="mt-4 text-[10px] text-zinc-600 font-body text-center">
        Want your results here?
        <router-link to="/settings" class="text-lime-400 hover:text-lime-300 ml-1">Enable sharing in Settings</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth.js'
import { apiFetch } from '../utils/api.js'

const authStore = useAuthStore()

const loading = ref(true)
const data = ref([])
const lastUpdated = ref('')
const searchQuery = ref('')
const filterProvider = ref('')
const sortKey = ref('tool_accuracy_pct')
const sortAsc = ref(false)

const PROVIDER_COLORS = {
  OpenAI: '#10B981',
  Anthropic: '#F97316',
  Google: '#3B82F6',
  'Google Gemini': '#3B82F6',
  Mistral: '#8B5CF6',
  Meta: '#EC4899',
  Cohere: '#14B8A6',
}

const allProviders = computed(() => {
  const ps = new Set(data.value.map(r => r.provider).filter(Boolean))
  return Array.from(ps).sort()
})

const filteredData = computed(() => {
  let rows = data.value
  if (filterProvider.value) {
    rows = rows.filter(r => r.provider === filterProvider.value)
  }
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.toLowerCase()
    rows = rows.filter(r =>
      (r.model_name || r.model_id || '').toLowerCase().includes(q) ||
      (r.provider || '').toLowerCase().includes(q)
    )
  }
  return rows
})

const sortedData = computed(() => {
  return [...filteredData.value].sort((a, b) => {
    const va = a[sortKey.value] ?? (sortKey.value === 'model_name' ? '' : -1)
    const vb = b[sortKey.value] ?? (sortKey.value === 'model_name' ? '' : -1)
    if (typeof va === 'string') {
      return sortAsc.value ? va.localeCompare(vb) : vb.localeCompare(va)
    }
    return sortAsc.value ? va - vb : vb - va
  })
})

function toggleSort(key) {
  if (sortKey.value === key) {
    sortAsc.value = !sortAsc.value
  } else {
    sortKey.value = key
    sortAsc.value = false
  }
}

function sortIndicator(key) {
  if (sortKey.value !== key) return ''
  return sortAsc.value ? '\u25B2' : '\u25BC'
}

function scoreColor(pct) {
  if (pct == null) return '#52525B'
  if (pct >= 80) return 'var(--lime,#BFFF00)'
  if (pct >= 50) return '#FBBF24'
  return 'var(--coral,#FF3B5C)'
}

function scoreGradient(pct) {
  if (pct == null || pct < 50) return 'var(--coral,#FF3B5C)'
  if (pct < 80) return '#FBBF24'
  return 'var(--lime,#BFFF00)'
}

function providerStyle(provider) {
  const color = PROVIDER_COLORS[provider]
  if (!color) return { background: 'rgba(255,255,255,0.04)', color: '#71717A' }
  return {
    background: color + '14',
    color,
    border: `1px solid ${color}33`,
  }
}

onMounted(async () => {
  try {
    const res = await apiFetch('/api/leaderboard/tool-eval')
    if (res.ok) {
      const json = await res.json()
      data.value = json.models || json.entries || []
      if (json.last_updated) {
        const d = new Date(json.last_updated)
        lastUpdated.value = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
      }
    }
  } catch {
    // Non-fatal: show empty state
  } finally {
    loading.value = false
  }
})
</script>
