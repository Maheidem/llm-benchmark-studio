<template>
  <div class="max-w-7xl mx-auto px-6 py-8">
    <!-- Access denied for non-admins -->
    <div v-if="!authStore.isAdmin" class="text-center py-16">
      <div class="text-2xl font-display font-bold text-zinc-500 mb-2">Access Denied</div>
      <p class="text-zinc-600 text-sm">You need admin privileges to view this page.</p>
    </div>

    <template v-else>
      <h1 class="font-display text-lg font-semibold text-zinc-200 uppercase tracking-wider mb-6">Admin Dashboard</h1>

      <!-- Stats -->
      <AdminStats ref="statsRef" />

      <!-- System Health -->
      <div class="card rounded-md p-4 mb-6">
        <h3 class="font-display font-semibold text-sm text-zinc-100 tracking-wider uppercase mb-4">System Health</h3>
        <div v-if="systemHealth" class="grid grid-cols-2 md:grid-cols-5 gap-4">
          <div>
            <div class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">DB Size</div>
            <div class="text-sm font-mono text-zinc-300">{{ systemHealth.db_size_mb }} MB</div>
          </div>
          <div>
            <div class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Results Files</div>
            <div class="text-sm font-mono text-zinc-300">{{ systemHealth.results_count }} ({{ systemHealth.results_size_mb }} MB)</div>
          </div>
          <div>
            <div class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Uptime</div>
            <div class="text-sm font-mono text-zinc-300">{{ formatUptime(systemHealth.process_uptime_s) }}</div>
          </div>
          <div>
            <div class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Active Jobs</div>
            <div class="text-sm font-mono" :class="systemHealth.total_active ? 'text-yellow-400' : 'text-zinc-500'">
              {{ systemHealth.total_active || 0 }} running, {{ systemHealth.total_queued || 0 }} queued
            </div>
          </div>
          <div>
            <div class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">WS Clients</div>
            <div class="text-sm font-mono text-zinc-300">{{ systemHealth.connected_ws_clients || 0 }}</div>
          </div>
        </div>
        <div v-else class="text-zinc-600 text-xs">Loading system health...</div>
      </div>

      <!-- Tab navigation -->
      <nav class="flex gap-2 mb-6 border-b border-zinc-800">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          :class="['tab px-4', { 'tab-active': activeTab === tab.id }]"
          @click="activeTab = tab.id"
        >{{ tab.label }}</button>
      </nav>

      <!-- Jobs tab -->
      <div v-show="activeTab === 'jobs'">
        <JobsTable :jobs="jobs" :loading="loadingJobs" @cancel="cancelJob" />
      </div>

      <!-- Users tab -->
      <div v-show="activeTab === 'users'">
        <UsersTable ref="usersRef" @stats-changed="loadStats" />
      </div>

      <!-- Audit Log tab -->
      <div v-show="activeTab === 'audit'">
        <AuditLog />
      </div>

      <!-- Logs tab -->
      <div v-show="activeTab === 'logs'">
        <LogsViewer />
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useAuthStore } from '../stores/auth.js'
import { apiFetch } from '../utils/api.js'
import { useToast } from '../composables/useToast.js'
import { useModal } from '../composables/useModal.js'
import AdminStats from '../components/admin/AdminStats.vue'
import JobsTable from '../components/admin/JobsTable.vue'
import UsersTable from '../components/admin/UsersTable.vue'
import AuditLog from '../components/admin/AuditLog.vue'
import LogsViewer from '../components/admin/LogsViewer.vue'

const authStore = useAuthStore()
const { showToast } = useToast()
const { confirm } = useModal()

const activeTab = ref('jobs')
const tabs = [
  { id: 'jobs', label: 'Active Jobs' },
  { id: 'users', label: 'Users' },
  { id: 'audit', label: 'Audit Log' },
  { id: 'logs', label: 'System Logs' },
]

const statsRef = ref(null)
const usersRef = ref(null)

// System health
const systemHealth = ref(null)

async function loadSystemHealth() {
  try {
    const res = await apiFetch('/api/admin/system')
    if (res.ok) systemHealth.value = await res.json()
  } catch { /* ignore */ }
}

// Jobs
const jobs = ref([])
const loadingJobs = ref(false)
let jobsInterval = null

async function loadJobs() {
  loadingJobs.value = true
  try {
    const res = await apiFetch('/api/admin/jobs')
    if (res.ok) {
      const data = await res.json()
      jobs.value = data.jobs || []
    }
  } catch { /* ignore */ }
  finally { loadingJobs.value = false }
}

async function cancelJob(jobId) {
  const confirmed = await confirm(
    'Cancel Process',
    'Cancel this process? This cannot be undone.',
    { danger: true, confirmLabel: 'Cancel Process' }
  )
  if (!confirmed) return

  try {
    const res = await apiFetch(`/api/admin/jobs/${jobId}/cancel`, { method: 'POST' })
    if (!res.ok) {
      showToast('Failed to cancel', 'error')
      return
    }
    showToast('Process cancelled', 'success')
    await loadJobs()
  } catch {
    showToast('Failed to cancel', 'error')
  }
}

function loadStats() {
  if (statsRef.value?.load) statsRef.value.load()
}

function formatUptime(seconds) {
  if (!seconds) return '-'
  const hrs = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  return `${hrs}h ${mins}m`
}

onMounted(() => {
  if (authStore.isAdmin) {
    loadSystemHealth()
    loadJobs()
    // Auto-refresh jobs every 15s
    jobsInterval = setInterval(loadJobs, 15000)
  }
})

onBeforeUnmount(() => {
  if (jobsInterval) clearInterval(jobsInterval)
})
</script>
