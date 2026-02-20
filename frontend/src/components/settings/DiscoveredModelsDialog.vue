<template>
  <Teleport to="body">
    <div v-if="visible" class="modal-overlay" @click.self="close">
      <div class="card rounded-md p-5 w-full max-w-lg max-h-[70vh] flex flex-col" style="border:1px solid var(--border-subtle)">
        <div class="flex items-center justify-between mb-3">
          <span class="font-display text-sm tracking-wide text-zinc-200">{{ models.length }} models available</span>
          <button @click="close" class="text-zinc-500 hover:text-zinc-300 text-xs cursor-pointer">Close</button>
        </div>
        <div class="overflow-y-auto flex-1 mb-3">
          <label
            v-for="(m, i) in models"
            :key="m.id"
            class="flex items-center gap-2 py-1.5 px-2 rounded hover:bg-white/5 cursor-pointer"
            :class="existingIds.has(m.id) ? 'opacity-40' : ''"
          >
            <input
              type="checkbox"
              :value="i"
              :checked="existingIds.has(m.id) || selected.has(i)"
              :disabled="existingIds.has(m.id)"
              @change="toggleSelect(i, $event)"
              class="accent-lime-400"
            >
            <span class="text-xs font-mono text-zinc-300">{{ m.id }}</span>
            <span v-if="m.display_name !== m.id" class="text-[10px] text-zinc-500">{{ m.display_name }}</span>
            <span v-if="existingIds.has(m.id)" class="text-[9px] text-zinc-600 ml-auto">already added</span>
          </label>
        </div>
        <button
          @click="addSelected"
          :disabled="adding"
          class="lime-btn self-end"
        >{{ adding ? 'Adding...' : 'Add Selected' }}</button>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  models: { type: Array, default: () => [] },
  existingModelIds: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'add'])

const selected = ref(new Set())
const adding = ref(false)

const existingIds = computed(() => new Set(props.existingModelIds))

function toggleSelect(i, event) {
  if (event.target.checked) {
    selected.value.add(i)
  } else {
    selected.value.delete(i)
  }
  // Force reactivity
  selected.value = new Set(selected.value)
}

function close() {
  selected.value = new Set()
  emit('close')
}

async function addSelected() {
  const toAdd = Array.from(selected.value).map(i => props.models[i])
  if (toAdd.length === 0) return
  adding.value = true
  emit('add', toAdd)
  // Parent will close us after processing
}

// Reset when dialog opens
function reset() {
  selected.value = new Set()
  adding.value = false
}

defineExpose({ reset })
</script>

<style scoped>
.lime-btn {
  font-size: 11px;
  font-family: 'Chakra Petch', sans-serif;
  font-weight: 500;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  padding: 8px 16px;
  border-radius: 2px;
  color: var(--lime);
  border: 1px solid rgba(191,255,0,0.2);
  background: transparent;
  cursor: pointer;
  transition: border-color 0.15s;
}
.lime-btn:hover {
  border-color: rgba(191,255,0,0.5);
}
.lime-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
