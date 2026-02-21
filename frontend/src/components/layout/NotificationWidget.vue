<template>
  <div class="relative ml-2" ref="widgetRef">
    <!-- Bell button -->
    <button class="notif-bell" @click="notifStore.toggleDropdown()" aria-label="Notifications" title="Running processes">
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" d="M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0" />
      </svg>
      <span
        class="notif-badge"
        :class="badgeStatusClass"
      >{{ notifStore.activeCount > 0 ? (notifStore.activeCount > 99 ? '99+' : notifStore.activeCount) : '' }}</span>
    </button>

    <!-- Dropdown panel -->
    <div class="notif-dropdown" :class="{ open: notifStore.dropdownOpen }" role="region" aria-label="Process notifications">
      <!-- Active section label -->
      <div class="notif-section-label">{{ activeSectionLabel }}</div>

      <!-- Active jobs list -->
      <div v-if="notifStore.activeJobs.length === 0">
        <div class="notif-empty">No active processes</div>
      </div>
      <template v-else>
        <!-- Running jobs -->
        <div
          v-for="job in runningJobs"
          :key="job.id"
          class="notif-item"
          style="cursor:pointer;"
          @click="navigateToRunning(job)"
        >
          <div :class="['notif-icon', job.job_type]">{{ getJobIcon(job.job_type) }}</div>
          <div class="notif-body">
            <div class="notif-title">{{ job.progress_detail || getJobLabel(job.job_type) }}</div>
            <div class="notif-progress">
              <div class="notif-progress-bar" :style="{ width: (job.progress_pct || 0) + '%' }"></div>
            </div>
            <div class="notif-meta">
              <span>Running &middot; {{ job.progress_pct || 0 }}%</span>
              <span>{{ timeAgo(job.created_at) }}</span>
            </div>
          </div>
          <button class="notif-cancel" @click.stop="handleCancel(job.id)" title="Cancel">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- Queued separator + items -->
        <div v-if="queuedJobs.length > 0 && runningJobs.length > 0" class="notif-section-label" style="padding-top:6px;">
          Queued ({{ queuedJobs.length }})
        </div>
        <div
          v-for="job in queuedJobs"
          :key="job.id"
          class="notif-item"
          style="cursor:pointer;"
          @click="navigateToRunning(job)"
        >
          <div :class="['notif-icon', job.job_type]">{{ getJobIcon(job.job_type) }}</div>
          <div class="notif-body">
            <div class="notif-title">{{ job.progress_detail || getJobLabel(job.job_type) }}</div>
            <div class="notif-meta">
              <span>{{ job.queued_position ? 'Queue position ' + job.queued_position : 'Queued' }}</span>
              <span>{{ timeAgo(job.created_at) }}</span>
            </div>
          </div>
          <button class="notif-cancel" @click.stop="handleCancel(job.id)" title="Cancel">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </template>

      <!-- Divider -->
      <div style="border-top:1px solid var(--border-subtle);"></div>

      <!-- Recent section -->
      <div class="notif-section-label">Recent</div>
      <div v-if="notifStore.recentJobs.length === 0">
        <div class="notif-empty">No recent processes</div>
      </div>
      <div
        v-for="job in notifStore.recentJobs"
        :key="job.id"
        class="notif-item"
        :style="{ cursor: job.status === 'done' && job.result_ref ? 'pointer' : 'default' }"
        :title="job.error_msg || ''"
        @click="navigateToResult(job)"
      >
        <div :class="['notif-icon', job.job_type]">{{ getJobIcon(job.job_type) }}</div>
        <div class="notif-body">
          <div class="notif-title">{{ job.progress_detail || getJobLabel(job.job_type) }}</div>
          <div class="notif-meta">
            <span :class="['notif-status-badge', job.status]">{{ statusLabel(job.status) }}</span>
            <span>{{ timeAgo(job.completed_at || job.created_at) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useNotificationsStore } from '../../stores/notifications.js'
import { JOB_TYPE_ICONS, JOB_TYPE_LABELS } from '../../utils/constants.js'
import { timeAgo } from '../../utils/helpers.js'
import { useToast } from '../../composables/useToast.js'

const notifStore = useNotificationsStore()
const router = useRouter()
const { showToast } = useToast()
const widgetRef = ref(null)

function getJobIcon(jobType) {
  return JOB_TYPE_ICONS[jobType] || '\u{1F4CB}'
}

function getJobLabel(jobType) {
  return JOB_TYPE_LABELS[jobType] || jobType
}

function statusLabel(status) {
  const labels = { done: 'Done', failed: 'Failed', cancelled: 'Cancelled', interrupted: 'Interrupted' }
  return labels[status] || status
}

const badgeStatusClass = computed(() => {
  return 'ws-' + notifStore.wsStatus
})

const runningJobs = computed(() => {
  return notifStore.activeJobs.filter(j => j.status === 'running')
})

const queuedJobs = computed(() => {
  return notifStore.activeJobs.filter(j => j.status === 'pending' || j.status === 'queued')
})

const activeSectionLabel = computed(() => {
  const r = runningJobs.value.length
  const q = queuedJobs.value.length
  if (r > 0 && q > 0) return `Running (${r}) \u00B7 Queued (${q})`
  if (r > 0) return `Running (${r})`
  if (q > 0) return `Queued (${q})`
  return 'Running'
})

async function handleCancel(jobId) {
  const result = await notifStore.cancelJob(jobId)
  if (result.success) {
    showToast(result.wasOrphan ? 'Run already stopped' : 'Process cancelled', '')
  } else {
    showToast(result.error, 'error')
  }
}

function navigateToRunning(job) {
  notifStore.closeDropdown()
  const routes = {
    benchmark: '/benchmark',
    tool_eval: '/tool-eval/evaluate',
    param_tune: '/tool-eval/param-tuner/run',
    prompt_tune: '/tool-eval/prompt-tuner/run',
    judge: '/tool-eval/judge',
    judge_compare: '/tool-eval/judge',
    scheduled_benchmark: '/schedules',
  }
  router.push(routes[job.job_type] || '/tool-eval')
}

function navigateToResult(job) {
  if (job.status !== 'done' || !job.result_ref) return
  notifStore.closeDropdown()
  const routes = {
    benchmark: '/history',
    tool_eval: '/tool-eval',
    param_tune: '/tool-eval/param-tuner/history',
    prompt_tune: '/tool-eval/prompt-tuner/history',
    judge: '/tool-eval/judge',
    judge_compare: '/tool-eval/judge',
    scheduled_benchmark: '/schedules',
  }
  router.push(routes[job.job_type] || '/tool-eval')
}

// Close dropdown on outside click
function handleOutsideClick(e) {
  if (widgetRef.value && !widgetRef.value.contains(e.target) && notifStore.dropdownOpen) {
    notifStore.closeDropdown()
  }
}

onMounted(() => {
  document.addEventListener('click', handleOutsideClick)
})

onUnmounted(() => {
  document.removeEventListener('click', handleOutsideClick)
})
</script>
