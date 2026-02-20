<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="font-display font-bold text-lg text-zinc-100 mb-1">Judge Compare</h2>
        <p class="text-sm text-zinc-600 font-body">Side-by-side comparison of two judge reports.</p>
      </div>
      <router-link :to="{ name: 'JudgeHistory' }"
        class="text-[10px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm"
        style="border:1px solid var(--border-subtle);color:var(--zinc-400);"
      >Back to Reports</router-link>
    </div>

    <div v-if="loading" class="text-xs text-zinc-600 font-body text-center py-8">Loading reports...</div>

    <JudgeCompareView
      v-else
      :report-a="reportA"
      :report-b="reportB"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useJudgeStore } from '../../stores/judge.js'
import { useToast } from '../../composables/useToast.js'
import JudgeCompareView from '../../components/tool-eval/JudgeCompareView.vue'

const route = useRoute()
const jgStore = useJudgeStore()
const { showToast } = useToast()

const loading = ref(true)
const reportA = ref(null)
const reportB = ref(null)

onMounted(async () => {
  const idA = route.query.a
  const idB = route.query.b

  if (!idA || !idB) {
    showToast('Two report IDs required for comparison', 'error')
    loading.value = false
    return
  }

  try {
    const [a, b] = await Promise.all([
      jgStore.loadReport(idA),
      jgStore.loadReport(idB),
    ])
    reportA.value = a
    reportB.value = b
  } catch {
    showToast('Failed to load reports', 'error')
  } finally {
    loading.value = false
  }
})
</script>
