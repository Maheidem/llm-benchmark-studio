<template>
  <Teleport to="body">
    <div
      v-if="modalState.visible"
      class="modal-overlay"
      :class="{ 'modal-closing': modalState.closing }"
      @click.self="handleCancel"
      @keydown.escape="handleCancel"
    >
      <div class="modal-box" role="dialog" aria-modal="true">
        <div class="modal-title">{{ modalState.title }}</div>

        <!-- Confirm mode -->
        <div v-if="modalState.mode === 'confirm'" class="modal-message" v-html="modalState.message"></div>

        <!-- Input mode -->
        <template v-if="modalState.mode === 'input'">
          <div v-if="modalState.message" class="modal-message">{{ modalState.message }}</div>
          <input
            ref="inputRef"
            v-model="modalState.values.value"
            :type="modalState.fields[0]?.type || 'text'"
            :placeholder="modalState.fields[0]?.placeholder || ''"
            class="modal-input"
            @keydown.enter="handleConfirm"
          />
        </template>

        <!-- Multi-field mode -->
        <template v-if="modalState.mode === 'multiField'">
          <div v-for="(field, i) in modalState.fields" :key="field.key" :style="{ marginBottom: i < modalState.fields.length - 1 ? '12px' : '16px' }">
            <div v-if="field.label" style="font-size:11px;color:#85858F;margin-bottom:4px;font-family:'Chakra Petch',sans-serif;text-transform:uppercase;letter-spacing:0.04em">
              {{ field.label }}
            </div>
            <input
              :ref="el => { if (i === 0) firstFieldRef = el }"
              v-model="modalState.values[field.key]"
              :type="field.type || 'text'"
              :placeholder="field.placeholder || ''"
              class="modal-input"
              style="margin-bottom:0"
              @keydown.enter="handleConfirm"
            />
          </div>
        </template>

        <div class="modal-buttons">
          <button class="modal-btn modal-btn-cancel" @click="handleCancel">
            {{ modalState.cancelLabel }}
          </button>
          <button
            :class="['modal-btn', modalState.danger ? 'modal-btn-danger' : 'modal-btn-confirm']"
            @click="handleConfirm"
          >
            {{ modalState.confirmLabel }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { useModal } from '../../composables/useModal.js'

const { modalState, close } = useModal()
const inputRef = ref(null)
const firstFieldRef = ref(null)

// Auto-focus input when modal opens
watch(() => modalState.visible, async (visible) => {
  if (visible && (modalState.mode === 'input' || modalState.mode === 'multiField')) {
    await nextTick()
    if (modalState.mode === 'input' && inputRef.value) {
      inputRef.value.focus()
    } else if (modalState.mode === 'multiField' && firstFieldRef.value) {
      firstFieldRef.value.focus()
    }
  }
})

function handleConfirm() {
  if (modalState.mode === 'confirm') {
    close(true)
  } else if (modalState.mode === 'input') {
    close(modalState.values.value)
  } else if (modalState.mode === 'multiField') {
    close({ ...modalState.values })
  }
}

function handleCancel() {
  if (modalState.mode === 'confirm') {
    close(false)
  } else {
    close(null)
  }
}
</script>
