<template>
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
    <div class="card rounded-md p-4 stat-card">
      <div class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Benchmarks (24h)</div>
      <div class="text-2xl font-display font-bold text-zinc-100">{{ stats.benchmarks_24h || 0 }}</div>
    </div>
    <div class="card rounded-md p-4 stat-card">
      <div class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Benchmarks (7d)</div>
      <div class="text-2xl font-display font-bold text-zinc-100">{{ stats.benchmarks_7d || 0 }}</div>
    </div>
    <div class="card rounded-md p-4 stat-card">
      <div class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Benchmarks (30d)</div>
      <div class="text-2xl font-display font-bold text-zinc-100">{{ stats.benchmarks_30d || 0 }}</div>
    </div>
    <div class="card rounded-md p-4 stat-card">
      <div class="text-[10px] font-display tracking-wider uppercase text-zinc-600 mb-1">Total Users</div>
      <div class="text-2xl font-display font-bold text-zinc-100">{{ stats.total_users || 0 }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { apiFetch } from '../../utils/api.js'

const stats = ref({})

async function load() {
  try {
    const res = await apiFetch('/api/admin/stats')
    if (res.ok) stats.value = await res.json()
  } catch { /* ignore */ }
}

defineExpose({ load })

onMounted(load)
</script>
