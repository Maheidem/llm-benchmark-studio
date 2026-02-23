<template>
  <div class="max-w-7xl mx-auto px-6 py-8">
    <!-- Sub-Tab Navigation -->
    <div class="flex items-center overflow-x-auto" style="border-bottom:1px solid var(--border-subtle, rgba(255,255,255,0.06));">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="te-subtab"
        :class="{ 'te-subtab-active': activeTab === tab.key }"
        @click="navigateTab(tab)"
      >{{ tab.label }}</button>
    </div>

    <!-- Context Bar -->
    <ContextBar
      @show-system-prompt="handleShowSystemPrompt"
      @newExperiment="onNewExperiment"
    />

    <!-- Child Route -->
    <div class="mt-6">
      <router-view />
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ContextBar from '../components/tool-eval/ContextBar.vue'
import { useToolEvalStore } from '../stores/toolEval.js'
import { useModal } from '../composables/useModal.js'
import { useSharedContext } from '../composables/useSharedContext.js'
import { useToast } from '../composables/useToast.js'
import { apiFetch } from '../utils/api.js'

const route = useRoute()
const router = useRouter()
const store = useToolEvalStore()
const { inputModal } = useModal()
const { context, setExperiment } = useSharedContext()
const { showToast } = useToast()

onMounted(() => {
  store.loadContext()
})

const tabs = [
  { key: 'suites', label: 'Suites', route: 'ToolEvalSuites' },
  { key: 'evaluate', label: 'Evaluate', route: 'ToolEvalEvaluate' },
  { key: 'param-tuner', label: 'Param Tuner', route: 'ParamTunerConfig' },
  { key: 'prompt-tuner', label: 'Prompt Tuner', route: 'PromptTunerConfig' },
  { key: 'prompt-library', label: 'Prompt Library', route: 'PromptLibrary' },
  { key: 'judge', label: 'Judge', route: 'JudgeHistory' },
  { key: 'timeline', label: 'Timeline', route: 'Timeline' },
  { key: 'history', label: 'History', route: 'ToolEvalHistory' },
]

// Map child routes to their parent tab key
const routeToTab = {
  ToolEvalSuites: 'suites',
  ToolEvalEditor: 'suites',
  ToolEvalEvaluate: 'evaluate',
  ParamTunerConfig: 'param-tuner',
  ParamTunerRun: 'param-tuner',
  ParamTunerHistory: 'param-tuner',
  PromptTunerConfig: 'prompt-tuner',
  PromptTunerRun: 'prompt-tuner',
  PromptTunerHistory: 'prompt-tuner',
  PromptLibrary: 'prompt-library',
  JudgeHistory: 'judge',
  JudgeCompare: 'judge',
  Timeline: 'timeline',
  ToolEvalHistory: 'history',
}

const activeTab = computed(() => {
  return routeToTab[route.name] || 'suites'
})

function navigateTab(tab) {
  router.push({ name: tab.route })
}

function navigateTo(name) {
  router.push({ name })
}

async function handleShowSystemPrompt() {
  await router.push({ name: 'ToolEvalEvaluate' })
  await nextTick()
  setTimeout(() => {
    const el = document.querySelector('[data-section="system-prompt"]')
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, 100)
}

async function onNewExperiment() {
  const name = await inputModal('New Experiment', 'Enter experiment name:')
  if (!name) return
  try {
    const res = await apiFetch('/api/experiments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, suite_id: context.suiteId || null })
    })
    if (!res.ok) throw new Error('Failed to create experiment')
    const data = await res.json()
    setExperiment(data.id, data.name || name)
    showToast('Experiment created', 'success')
  } catch (e) {
    showToast(e.message || 'Failed to create experiment', 'error')
  }
}
</script>
