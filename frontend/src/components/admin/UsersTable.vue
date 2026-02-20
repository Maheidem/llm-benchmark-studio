<template>
  <div class="overflow-x-auto">
    <div v-if="loading" class="text-zinc-600 text-xs py-3">Loading users...</div>
    <div v-else-if="!users.length" class="text-zinc-600 text-sm py-3">No users found.</div>
    <table v-else class="w-full text-xs results-table">
      <thead>
        <tr class="text-[10px] font-display tracking-wider uppercase text-zinc-500">
          <th class="text-left px-3 py-2">Email</th>
          <th class="text-left px-3 py-2">Role</th>
          <th class="text-right px-3 py-2">Benchmarks</th>
          <th class="text-right px-3 py-2">Keys</th>
          <th class="text-left px-3 py-2">Last Login</th>
          <th class="text-left px-3 py-2">Created</th>
          <th class="text-right px-3 py-2">Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="u in users" :key="u.id">
          <td class="px-3 py-2 font-mono text-zinc-300">{{ u.email }}</td>
          <td class="px-3 py-2">
            <select
              :value="u.role"
              :disabled="isCurrentUser(u.id)"
              :title="isCurrentUser(u.id) ? 'Cannot change own role' : ''"
              class="text-xs font-mono px-1 py-0.5 rounded-sm outline-none"
              :style="{
                background: 'rgba(255,255,255,0.02)',
                border: '1px solid var(--border-subtle)',
                color: u.role === 'admin' ? '#BFFF00' : '#A1A1AA'
              }"
              @change="changeRole(u.id, $event.target.value)"
            >
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </td>
          <td class="px-3 py-2 text-right font-mono text-zinc-400">{{ u.benchmark_count || 0 }}</td>
          <td class="px-3 py-2 text-right font-mono text-zinc-400">{{ u.key_count || 0 }}</td>
          <td class="px-3 py-2 text-zinc-500">{{ formatLogin(u.last_login) }}</td>
          <td class="px-3 py-2 text-zinc-500">{{ formatDate(u.created_at) }}</td>
          <td class="px-3 py-2 text-right whitespace-nowrap">
            <button
              class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm border border-[var(--border-subtle)] text-zinc-500 hover:text-zinc-300 transition-colors"
              @click="showRateLimit(u)"
            >Limits</button>
            <button
              v-if="!isCurrentUser(u.id)"
              class="text-[10px] font-display tracking-wider uppercase px-2 py-1 rounded-sm ml-1 border border-[var(--coral)] text-[var(--coral)] hover:bg-[rgba(255,59,92,0.1)] transition-colors"
              @click="deleteUser(u)"
            >Delete</button>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- Rate Limit Modal -->
    <Teleport to="body">
      <div
        v-if="rateLimitModal.visible"
        class="modal-overlay"
        @click.self="rateLimitModal.visible = false"
      >
        <div class="modal-box" style="max-width: 380px">
          <h3 class="font-display font-semibold text-sm text-zinc-100 mb-1">Rate Limits</h3>
          <p class="text-xs text-zinc-500 mb-4">{{ rateLimitModal.email }}</p>
          <div class="space-y-3">
            <div>
              <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Benchmarks Per Hour</label>
              <input
                v-model.number="rateLimitModal.bph"
                type="number"
                min="1"
                max="1000"
                class="modal-input w-full"
              />
            </div>
            <div>
              <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Max Concurrent</label>
              <input
                v-model.number="rateLimitModal.mc"
                type="number"
                min="1"
                max="10"
                class="modal-input w-full"
              />
            </div>
            <div>
              <label class="text-[10px] text-zinc-600 font-display tracking-wider uppercase block mb-1">Max Runs Per Benchmark</label>
              <input
                v-model.number="rateLimitModal.mrpb"
                type="number"
                min="1"
                max="50"
                class="modal-input w-full"
              />
            </div>
          </div>
          <div class="flex gap-2 mt-5">
            <button class="modal-btn flex-1" @click="rateLimitModal.visible = false">Cancel</button>
            <button class="modal-btn modal-btn-confirm flex-1" @click="saveRateLimit">Save</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { apiFetch } from '../../utils/api.js'
import { useAuthStore } from '../../stores/auth.js'
import { useToast } from '../../composables/useToast.js'
import { useModal } from '../../composables/useModal.js'

const emit = defineEmits(['stats-changed'])

const authStore = useAuthStore()
const { showToast } = useToast()
const { confirm } = useModal()

const users = ref([])
const loading = ref(false)

const rateLimitModal = reactive({
  visible: false,
  userId: '',
  email: '',
  bph: 20,
  mc: 1,
  mrpb: 10,
})

function isCurrentUser(userId) {
  return authStore.user && userId === authStore.user.id
}

function formatLogin(iso) {
  if (!iso) return 'Never'
  return new Date(iso + 'Z').toLocaleDateString()
}

function formatDate(iso) {
  if (!iso) return '-'
  return new Date(iso + 'Z').toLocaleDateString()
}

async function loadUsers() {
  loading.value = true
  try {
    const res = await apiFetch('/api/admin/users')
    if (res.ok) {
      const data = await res.json()
      users.value = data.users || []
    }
  } catch { /* ignore */ }
  finally { loading.value = false }
}

async function changeRole(userId, newRole) {
  try {
    const res = await apiFetch(`/api/admin/users/${userId}/role`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: newRole }),
    })
    if (res.ok) {
      showToast('Role updated', 'success')
      // Update local state
      const user = users.value.find(u => u.id === userId)
      if (user) user.role = newRole
    } else {
      const data = await res.json()
      showToast(data.error || 'Failed to update role', 'error')
      loadUsers()
    }
  } catch {
    showToast('Failed to update role', 'error')
    loadUsers()
  }
}

async function deleteUser(user) {
  const confirmed = await confirm(
    'Delete User',
    `Delete user "${user.email}"? This will permanently remove the user and ALL their data. This cannot be undone.`,
    { danger: true, confirmLabel: 'Delete' }
  )
  if (!confirmed) return

  try {
    const res = await apiFetch(`/api/admin/users/${user.id}`, { method: 'DELETE' })
    if (res.ok) {
      showToast('User deleted', 'success')
      loadUsers()
      emit('stats-changed')
    } else {
      const data = await res.json()
      showToast(data.error || 'Failed to delete user', 'error')
    }
  } catch {
    showToast('Failed to delete user', 'error')
  }
}

async function showRateLimit(user) {
  try {
    const res = await apiFetch(`/api/admin/users/${user.id}/rate-limit`)
    const limits = res.ok
      ? await res.json()
      : { benchmarks_per_hour: 20, max_concurrent: 1, max_runs_per_benchmark: 10 }

    rateLimitModal.userId = user.id
    rateLimitModal.email = user.email
    rateLimitModal.bph = limits.benchmarks_per_hour
    rateLimitModal.mc = limits.max_concurrent
    rateLimitModal.mrpb = limits.max_runs_per_benchmark
    rateLimitModal.visible = true
  } catch { /* ignore */ }
}

async function saveRateLimit() {
  try {
    const res = await apiFetch(`/api/admin/users/${rateLimitModal.userId}/rate-limit`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        benchmarks_per_hour: rateLimitModal.bph || 20,
        max_concurrent: rateLimitModal.mc || 1,
        max_runs_per_benchmark: rateLimitModal.mrpb || 10,
      }),
    })
    if (res.ok) {
      showToast('Rate limits updated', 'success')
      rateLimitModal.visible = false
    } else {
      const data = await res.json()
      showToast(data.error || 'Failed to save', 'error')
    }
  } catch {
    showToast('Failed to save rate limits', 'error')
  }
}

onMounted(loadUsers)
</script>
