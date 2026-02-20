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
      @show-system-prompt="navigateTo('ToolEvalEvaluate')"
    />

    <!-- Child Route -->
    <div class="mt-6">
      <router-view />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ContextBar from '../components/tool-eval/ContextBar.vue'
import { useToolEvalStore } from '../stores/toolEval.js'

const route = useRoute()
const router = useRouter()
const store = useToolEvalStore()

onMounted(() => {
  store.loadContext()
})

const tabs = [
  { key: 'suites', label: 'Suites', route: 'ToolEvalSuites' },
  { key: 'evaluate', label: 'Evaluate', route: 'ToolEvalEvaluate' },
  { key: 'param-tuner', label: 'Param Tuner', route: 'ParamTunerConfig' },
  { key: 'prompt-tuner', label: 'Prompt Tuner', route: 'PromptTunerConfig' },
  { key: 'judge', label: 'Judge', route: 'JudgeHistory' },
  { key: 'timeline', label: 'Timeline', route: 'Timeline' },
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
  JudgeHistory: 'judge',
  JudgeCompare: 'judge',
  Timeline: 'timeline',
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
</script>
