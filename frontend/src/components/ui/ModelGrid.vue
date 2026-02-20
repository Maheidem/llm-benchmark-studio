<template>
  <div>
    <!-- Search + select controls -->
    <div class="flex items-center gap-4 mb-4">
      <div class="flex-1 relative">
        <input
          v-model="search"
          type="text"
          placeholder="Search models..."
          class="w-full px-3 py-2 text-sm rounded-sm prompt-input"
        />
      </div>
      <button
        class="text-[11px] font-display tracking-wider uppercase px-3 py-1.5 rounded-sm transition-colors"
        style="color:var(--lime);background:var(--lime-dim);border:1px solid rgba(191,255,0,0.3);"
        @click="$emit('selectAll')"
      >
        Select All
      </button>
      <button
        class="text-[11px] font-display tracking-wider uppercase text-zinc-500 px-3 py-1.5 rounded-sm transition-colors hover:text-zinc-300"
        style="background:rgba(255,255,255,0.02);border:1px solid var(--border-subtle);"
        @click="$emit('selectNone')"
      >
        Select None
      </button>
    </div>

    <!-- Provider groups -->
    <div class="flex flex-col gap-2">
      <div
        v-for="group in filteredGroups"
        :key="group.name"
        class="provider-group"
        :style="{ borderColor: getColor(group.name).border }"
      >
        <!-- Provider header -->
        <div
          class="provider-group-header"
          @click="$emit('toggleProvider', group.name)"
        >
          <div class="provider-group-dot" :style="{ background: getColor(group.name).text }"></div>
          <span class="provider-group-label" :style="{ color: getColor(group.name).text }">
            {{ group.name }}
          </span>
          <span class="provider-group-count">
            {{ group.models.length }} model{{ group.models.length > 1 ? 's' : '' }}
          </span>
        </div>

        <!-- Model cards -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 pl-3">
          <div
            v-for="m in group.models"
            :key="m.compoundKey"
            class="model-card rounded-sm px-4 py-3 flex items-center gap-3"
            :class="{ selected: isSelected(m.compoundKey) }"
            @click.stop="$emit('toggle', m.compoundKey)"
          >
            <div class="check-dot flex-shrink-0"></div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <div class="text-[13px] font-medium text-zinc-200 truncate font-body">
                  {{ m.display_name }}
                </div>
                <span
                  v-if="m.context_window"
                  class="text-[9px] font-mono px-1.5 py-0.5 rounded-sm"
                  style="background:rgba(255,255,255,0.04);color:#85858F;"
                >
                  {{ formatCtxSize(m.context_window) }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-if="filteredGroups.length === 0" class="text-center py-8 text-zinc-600 text-sm">
      No models match your search.
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { getColor } from '../../utils/constants.js'
import { formatCtxSize } from '../../utils/helpers.js'

const props = defineProps({
  providers: { type: Array, required: true },
  selectedModels: { type: Set, required: true },
})

defineEmits(['toggle', 'selectAll', 'selectNone', 'toggleProvider'])

const search = ref('')

const filteredGroups = computed(() => {
  const q = search.value.toLowerCase().trim()
  return props.providers
    .map(p => {
      const models = p.models.map(m => ({
        ...m,
        compoundKey: (p.provider_key || p.name) + '::' + m.model_id,
      }))
      const filtered = q
        ? models.filter(m =>
            m.display_name.toLowerCase().includes(q) ||
            m.model_id.toLowerCase().includes(q) ||
            p.name.toLowerCase().includes(q)
          )
        : models
      return { name: p.name, provider_key: p.provider_key, models: filtered }
    })
    .filter(g => g.models.length > 0)
})

function isSelected(compoundKey) {
  return props.selectedModels.has(compoundKey)
}
</script>
