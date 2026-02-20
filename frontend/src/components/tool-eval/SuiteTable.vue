<template>
  <div class="card rounded-md overflow-hidden">
    <table class="w-full text-sm results-table">
      <thead>
        <tr style="border-bottom: 1px solid var(--border-subtle)">
          <th class="px-5 py-3 text-left section-label cursor-pointer" @click="sort('name')">
            Name {{ sortKey === 'name' ? (sortAsc ? '\u25B2' : '\u25BC') : '' }}
          </th>
          <th class="px-5 py-3 text-center section-label cursor-pointer" @click="sort('tool_count')">
            Tools {{ sortKey === 'tool_count' ? (sortAsc ? '\u25B2' : '\u25BC') : '' }}
          </th>
          <th class="px-5 py-3 text-center section-label cursor-pointer" @click="sort('test_case_count')">
            Cases {{ sortKey === 'test_case_count' ? (sortAsc ? '\u25B2' : '\u25BC') : '' }}
          </th>
          <th class="px-5 py-3 text-right section-label">Last Updated</th>
          <th class="px-5 py-3 text-right section-label"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!suites.length">
          <td colspan="5" class="px-5 py-8 text-center text-zinc-600 text-sm font-body">
            No tool suites yet. Click "New Suite" to create one.
          </td>
        </tr>
        <tr v-for="s in sortedSuites" :key="s.id">
          <td class="px-5 py-3">
            <button
              @click="$emit('select', s.id)"
              class="text-zinc-200 hover:text-white font-body text-sm underline decoration-zinc-700 hover:decoration-zinc-400 transition-colors"
            >{{ s.name || 'Untitled' }}</button>
          </td>
          <td class="px-5 py-3 text-center font-mono text-xs text-zinc-500">{{ s.tool_count || 0 }}</td>
          <td class="px-5 py-3 text-center font-mono text-xs text-zinc-500">{{ s.test_case_count || 0 }}</td>
          <td class="px-5 py-3 text-right text-xs text-zinc-600 font-body">{{ formatTime(s.updated_at) }}</td>
          <td class="px-5 py-3 text-right">
            <div class="flex items-center justify-end gap-3">
              <button
                @click="$emit('export', s.id)"
                class="text-zinc-700 hover:text-emerald-400 transition-colors text-xs font-display tracking-wider uppercase"
              >Export</button>
              <button
                @click="$emit('delete', s.id, s.name)"
                class="text-zinc-700 hover:text-red-400 transition-colors text-xs font-display tracking-wider uppercase"
              >Delete</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { timeAgo } from '../../utils/helpers.js'

const props = defineProps({
  suites: { type: Array, required: true },
})

defineEmits(['select', 'clone', 'delete', 'export'])

const sortKey = ref('name')
const sortAsc = ref(true)

function sort(key) {
  if (sortKey.value === key) {
    sortAsc.value = !sortAsc.value
  } else {
    sortKey.value = key
    sortAsc.value = key === 'name'
  }
}

const sortedSuites = computed(() => {
  const arr = [...props.suites]
  arr.sort((a, b) => {
    const aVal = a[sortKey.value] ?? ''
    const bVal = b[sortKey.value] ?? ''
    if (typeof aVal === 'string') {
      return sortAsc.value ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
    }
    return sortAsc.value ? aVal - bVal : bVal - aVal
  })
  return arr
})

function formatTime(dateStr) {
  if (!dateStr) return ''
  return timeAgo(dateStr)
}
</script>
