import { ref } from 'vue'

const toasts = ref([])
let toastId = 0

export function useToast() {
  function showToast(message, type = '') {
    const id = ++toastId
    toasts.value.push({ id, message, type, removing: false })
    setTimeout(() => removeToast(id), 4000)
  }

  function removeToast(id) {
    const t = toasts.value.find(t => t.id === id)
    if (t) {
      t.removing = true
      setTimeout(() => {
        toasts.value = toasts.value.filter(t => t.id !== id)
      }, 300)
    }
  }

  return { toasts, showToast, removeToast }
}
