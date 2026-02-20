<template>
  <div>
    <!-- Filters -->
    <div class="flex items-center gap-3 mb-4 flex-wrap">
      <select
        v-model="filters.user"
        class="text-xs font-mono px-2 py-1 rounded-sm bg-[rgba(255,255,255,0.02)] border border-[var(--border-subtle)] text-zinc-300 outline-none"
        @change="resetAndLoad"
      >
        <option value="">All Users</option>
        <option v-for="u in userOptions" :key="u" :value="u">{{ u }}</option>
      </select>

      <select
        v-model="filters.action"
        class="text-xs font-mono px-2 py-1 rounded-sm bg-[rgba(255,255,255,0.02)] border border-[var(--border-subtle)] text-zinc-300 outline-none"
        @change="resetAndLoad"
      >
        <option value="">All Actions</option>
        <option v-for="a in actionOptions" :key="a" :value="a">{{ a }}</option>
      </select>

      <select
        v-model="filters.since"
        class="text-xs font-mono px-2 py-1 rounded-sm bg-[rgba(255,255,255,0.02)] border border-[var(--border-subtle)] text-zinc-300 outline-none"
        @change="resetAndLoad"
      >
        <option value="">All Time</option>
        <option value="1h">Last Hour</option>
        <option value="24h">Last 24 Hours</option>
        <option value="7d">Last 7 Days</option>
        <option value="30d">Last 30 Days</option>
      </select>
    </div>

    <!-- Table -->
    <div class="overflow-x-auto">
      <div v-if="loading" class="text-zinc-600 text-xs py-3">Loading audit log...</div>
      <div v-else-if="!entries.length" class="text-zinc-600 text-sm py-3">No audit entries found.</div>
      <table v-else class="w-full text-xs results-table">
        <thead>
          <tr class="text-[10px] font-display tracking-wider uppercase text-zinc-500">
            <th class="text-left px-3 py-2">Time</th>
            <th class="text-left px-3 py-2">User</th>
            <th class="text-left px-3 py-2">Action</th>
            <th class="text-left px-3 py-2">Resource</th>
            <th class="text-left px-3 py-2">Detail</th>
            <th class="text-left px-3 py-2">IP</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in entries" :key="e.id || e.timestamp">
            <td class="px-3 py-2 text-zinc-500 whitespace-nowrap">{{ formatTime(e.timestamp) }}</td>
            <td class="px-3 py-2 font-mono text-zinc-400">{{ e.username || '' }}</td>
            <td class="px-3 py-2 font-mono" :class="actionColor(e.action)">{{ e.action || '' }}</td>
            <td class="px-3 py-2 text-zinc-500">
              {{ e.resource_type || '' }}{{ e.resource_id ? ':' + e.resource_id : '' }}
            </td>
            <td
              class="px-3 py-2 text-zinc-600 font-mono max-w-[200px] truncate"
              :title="formatDetail(e.detail)"
            >{{ formatDetail(e.detail) }}</td>
            <td class="px-3 py-2 text-zinc-600 font-mono">{{ e.ip_address || '' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div class="flex items-center justify-between mt-3">
      <span class="text-[10px] font-mono text-zinc-600">{{ pageInfo }}</span>
      <div class="flex gap-2">
        <button
          :disabled="page === 0"
          class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm border border-[var(--border-subtle)] text-zinc-500 disabled:opacity-30 hover:text-zinc-300 transition-colors"
          @click="prevPage"
        >Prev</button>
        <button
          :disabled="entries.length < PAGE_SIZE"
          class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm border border-[var(--border-subtle)] text-zinc-500 disabled:opacity-30 hover:text-zinc-300 transition-colors"
          @click="nextPage"
        >Next</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { apiFetch } from '../../utils/api.js'

const PAGE_SIZE = 50

const ACTION_COLORS = {
  user_login: 'text-green-400',
  user_login_failed: 'text-red-400',
  user_register: 'text-blue-400',
  user_logout: 'text-zinc-500',
  benchmark_start: 'text-yellow-400',
  benchmark_complete: 'text-green-400',
  benchmark_cancel: 'text-orange-400',
  admin_user_update: 'text-purple-400',
  admin_user_delete: 'text-red-400',
  admin_rate_limit: 'text-purple-400',
  token_refresh: 'text-zinc-500',
}

const entries = ref([])
const loading = ref(false)
const page = ref(0)
const userOptions = ref([])
const filters = reactive({
  user: '',
  action: '',
  since: '',
})

const actionOptions = [
  'user_login', 'user_login_failed', 'user_register', 'user_logout',
  'benchmark_start', 'benchmark_complete', 'benchmark_cancel',
  'admin_user_update', 'admin_user_delete', 'admin_rate_limit', 'token_refresh',
]

const pageInfo = computed(() => {
  if (!entries.value.length) return ''
  const from = page.value * PAGE_SIZE + 1
  const to = from + entries.value.length - 1
  return `${from}-${to}`
})

function formatTime(ts) {
  if (!ts) return '-'
  return new Date(ts + 'Z').toLocaleString()
}

function actionColor(action) {
  return ACTION_COLORS[action] || 'text-zinc-400'
}

function formatDetail(detail) {
  if (!detail) return ''
  let str = typeof detail === 'string' ? detail : JSON.stringify(detail)
  if (str.length > 60) str = str.slice(0, 57) + '...'
  return str
}

function sinceParam() {
  if (!filters.since) return ''
  const now = new Date()
  const map = { '1h': 3600000, '24h': 86400000, '7d': 604800000, '30d': 2592000000 }
  const ms = map[filters.since] || 0
  if (!ms) return ''
  return new Date(now.getTime() - ms).toISOString().replace('T', ' ').slice(0, 19)
}

async function loadAuditLog() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (filters.user) params.set('user', filters.user)
    if (filters.action) params.set('action', filters.action)
    const since = sinceParam()
    if (since) params.set('since', since)
    params.set('limit', PAGE_SIZE)
    params.set('offset', page.value * PAGE_SIZE)

    const res = await apiFetch(`/api/admin/audit?${params.toString()}`)
    if (res.ok) {
      const data = await res.json()
      entries.value = data.entries || []
    }
  } catch { /* ignore */ }
  finally { loading.value = false }
}

async function loadUserOptions() {
  try {
    const res = await apiFetch('/api/admin/stats')
    if (res.ok) {
      const stats = await res.json()
      userOptions.value = (stats.top_users || []).map(u => u.username)
    }
  } catch { /* ignore */ }
}

function resetAndLoad() {
  page.value = 0
  loadAuditLog()
}

function prevPage() {
  if (page.value > 0) {
    page.value--
    loadAuditLog()
  }
}

function nextPage() {
  page.value++
  loadAuditLog()
}

onMounted(() => {
  loadUserOptions()
  loadAuditLog()
})
</script>
