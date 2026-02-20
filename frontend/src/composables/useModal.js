import { reactive } from 'vue'

const modalState = reactive({
  visible: false,
  closing: false,
  title: '',
  message: '',
  mode: 'confirm', // 'confirm' | 'input' | 'multiField'
  fields: [],
  values: {},
  confirmLabel: 'Confirm',
  cancelLabel: 'Cancel',
  danger: false,
  resolve: null,
})

export function useModal() {
  function confirm(title, message, opts = {}) {
    return new Promise(resolve => {
      Object.assign(modalState, {
        visible: true,
        closing: false,
        title,
        message,
        mode: 'confirm',
        fields: [],
        values: {},
        danger: opts.danger || false,
        confirmLabel: opts.confirmLabel || (opts.danger ? 'Delete' : 'Confirm'),
        cancelLabel: opts.cancelLabel || 'Cancel',
        resolve,
      })
    })
  }

  function inputModal(title, placeholder, opts = {}) {
    return new Promise(resolve => {
      Object.assign(modalState, {
        visible: true,
        closing: false,
        title,
        message: opts.message || '',
        mode: 'input',
        fields: [{ key: 'value', placeholder, type: opts.type || 'text', label: '' }],
        values: { value: opts.defaultValue || '' },
        confirmLabel: opts.confirmLabel || 'OK',
        cancelLabel: 'Cancel',
        danger: false,
        resolve,
      })
    })
  }

  function multiFieldModal(title, fields, opts = {}) {
    return new Promise(resolve => {
      const values = {}
      fields.forEach(f => { values[f.key] = f.defaultValue || '' })
      Object.assign(modalState, {
        visible: true,
        closing: false,
        title,
        message: '',
        mode: 'multiField',
        fields,
        values,
        confirmLabel: opts.confirmLabel || 'Save',
        cancelLabel: opts.cancelLabel || 'Cancel',
        danger: false,
        resolve,
      })
    })
  }

  function close(result) {
    modalState.closing = true
    setTimeout(() => {
      modalState.visible = false
      modalState.closing = false
      if (modalState.resolve) {
        modalState.resolve(result)
        modalState.resolve = null
      }
    }, 200)
  }

  return { modalState, confirm, inputModal, multiFieldModal, close }
}
