<template>
  <div>
    <!-- Search -->
    <div v-if="searchable" class="mb-3">
      <input
        v-model="searchQuery"
        type="text"
        placeholder="Search..."
        class="w-full px-3 py-2 rounded-sm text-sm font-mono text-zinc-200"
        style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);outline:none;"
        @focus="$event.target.style.borderColor = 'rgba(191,255,0,0.3)'"
        @blur="$event.target.style.borderColor = 'var(--border-subtle)'"
      >
    </div>

    <!-- Table -->
    <div class="overflow-x-auto">
      <table class="w-full text-xs results-table">
        <thead>
          <tr style="border-bottom:1px solid var(--border-subtle)">
            <th
              v-for="col in columns"
              :key="col.key"
              class="px-3 py-2 section-label cursor-pointer select-none"
              :class="col.align === 'right' ? 'text-right' : 'text-left'"
              @click="col.sortable !== false && toggleSort(col.key)"
            >
              <span class="inline-flex items-center gap-1">
                {{ col.label }}
                <span v-if="sortKey === col.key" class="text-[9px]">{{ sortDir === 'asc' ? '\u25B2' : '\u25BC' }}</span>
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, i) in paginatedRows"
            :key="i"
            style="border-top:1px solid var(--border-subtle)"
            class="cursor-pointer"
            @click="$emit('rowClick', row)"
          >
            <td
              v-for="col in columns"
              :key="col.key"
              class="px-3 py-2 font-body"
              :class="[
                col.align === 'right' ? 'text-right font-mono' : 'text-left',
                col.class || 'text-zinc-400',
              ]"
            >
              <slot :name="`cell-${col.key}`" :row="row" :value="getCellValue(row, col)">
                {{ formatCell(row, col) }}
              </slot>
            </td>
          </tr>
          <tr v-if="paginatedRows.length === 0">
            <td :colspan="columns.length" class="px-3 py-6 text-center text-zinc-600 font-body">No data</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div v-if="pageSize > 0 && totalPages > 1" class="flex items-center justify-between mt-3">
      <span class="text-[10px] text-zinc-600 font-body">{{ filteredRows.length }} items</span>
      <div class="flex gap-1">
        <button
          v-for="p in totalPages"
          :key="p"
          @click="currentPage = p"
          class="text-[10px] font-mono px-2 py-1 rounded-sm transition-colors"
          :class="currentPage === p ? '' : 'text-zinc-600 hover:text-zinc-400'"
          :style="currentPage === p ? 'background:var(--lime-dim);color:var(--lime);border:1px solid rgba(191,255,0,0.3)' : 'background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle)'"
        >{{ p }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  columns: { type: Array, required: true },
  rows: { type: Array, required: true },
  searchable: { type: Boolean, default: false },
  searchKeys: { type: Array, default: () => [] },
  pageSize: { type: Number, default: 0 },
})

defineEmits(['rowClick'])

const searchQuery = ref('')
const sortKey = ref('')
const sortDir = ref('desc')
const currentPage = ref(1)

// Reset page on search
watch(searchQuery, () => { currentPage.value = 1 })

function toggleSort(key) {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortDir.value = 'desc'
  }
}

function getCellValue(row, col) {
  const val = row[col.key]
  return val
}

function formatCell(row, col) {
  const val = row[col.key]
  if (val == null) return '-'
  if (col.format) return col.format(val, row)
  return String(val)
}

const filteredRows = computed(() => {
  let rows = [...props.rows]
  // Search
  if (searchQuery.value && props.searchKeys.length > 0) {
    const q = searchQuery.value.toLowerCase()
    rows = rows.filter(row =>
      props.searchKeys.some(key => {
        const val = row[key]
        return val != null && String(val).toLowerCase().includes(q)
      })
    )
  }
  // Sort
  if (sortKey.value) {
    rows.sort((a, b) => {
      const va = a[sortKey.value]
      const vb = b[sortKey.value]
      if (va == null && vb == null) return 0
      if (va == null) return 1
      if (vb == null) return -1
      if (typeof va === 'number' && typeof vb === 'number') {
        return sortDir.value === 'asc' ? va - vb : vb - va
      }
      const sa = String(va).toLowerCase()
      const sb = String(vb).toLowerCase()
      return sortDir.value === 'asc' ? sa.localeCompare(sb) : sb.localeCompare(sa)
    })
  }
  return rows
})

const totalPages = computed(() => {
  if (props.pageSize <= 0) return 1
  return Math.ceil(filteredRows.value.length / props.pageSize)
})

const paginatedRows = computed(() => {
  if (props.pageSize <= 0) return filteredRows.value
  const start = (currentPage.value - 1) * props.pageSize
  return filteredRows.value.slice(start, start + props.pageSize)
})
</script>
