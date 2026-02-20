<template>
  <div class="max-w-7xl mx-auto px-6 py-8">
    <div class="flex items-center justify-between mb-6">
      <h1 class="font-display text-lg font-semibold text-zinc-200 uppercase tracking-wider">Schedules</h1>
      <button
        class="text-[11px] font-display tracking-wider uppercase px-4 py-2 rounded-sm text-[var(--lime)] border border-[rgba(191,255,0,0.3)] bg-[var(--lime-dim)] hover:bg-[rgba(191,255,0,0.15)] transition-all"
        @click="showNewModal = true"
      >
        New Schedule
      </button>
    </div>

    <ScheduleTable
      :schedules="schedules"
      :loading="loading"
      @toggle-enabled="toggleEnabled"
      @trigger="triggerSchedule"
      @delete="deleteSchedule"
    />

    <NewScheduleModal
      v-if="showNewModal"
      @close="showNewModal = false"
      @created="onScheduleCreated"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { apiFetch } from '../utils/api.js'
import { useToast } from '../composables/useToast.js'
import { useModal } from '../composables/useModal.js'
import ScheduleTable from '../components/schedules/ScheduleTable.vue'
import NewScheduleModal from '../components/schedules/NewScheduleModal.vue'

const { showToast } = useToast()
const { confirm } = useModal()

const schedules = ref([])
const loading = ref(false)
const showNewModal = ref(false)

async function loadSchedules() {
  loading.value = true
  try {
    const res = await apiFetch('/api/schedules')
    const data = await res.json()
    schedules.value = data.schedules || data || []
  } catch {
    schedules.value = []
  } finally {
    loading.value = false
  }
}

async function toggleEnabled(id, enabled) {
  try {
    await apiFetch(`/api/schedules/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    showToast(enabled ? 'Schedule enabled' : 'Schedule paused', 'success')
    // Update local state
    const sched = schedules.value.find(s => s.id === id)
    if (sched) sched.enabled = enabled ? 1 : 0
  } catch {
    showToast('Failed to update schedule', 'error')
    loadSchedules()
  }
}

async function triggerSchedule(id) {
  try {
    await apiFetch(`/api/schedules/${id}/trigger`, { method: 'POST' })
    showToast('Schedule triggered - benchmark starting', 'success')
    loadSchedules()
  } catch {
    showToast('Failed to trigger schedule', 'error')
  }
}

async function deleteSchedule(id, name) {
  const confirmed = await confirm(
    'Delete Schedule',
    `Delete schedule "${name}"? This cannot be undone.`,
    { danger: true, confirmLabel: 'Delete' }
  )
  if (!confirmed) return

  try {
    await apiFetch(`/api/schedules/${id}`, { method: 'DELETE' })
    showToast('Schedule deleted', 'success')
    loadSchedules()
  } catch {
    showToast('Failed to delete schedule', 'error')
  }
}

function onScheduleCreated() {
  showNewModal.value = false
  showToast('Schedule created', 'success')
  loadSchedules()
}

onMounted(loadSchedules)
</script>
