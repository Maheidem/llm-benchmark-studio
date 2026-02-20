<template>
  <div v-if="models.length > 0 && enabledParams.length > 0" class="card rounded-md overflow-hidden mb-6">
    <div class="px-5 py-3 flex items-center justify-between" style="border-bottom:1px solid var(--border-subtle);">
      <span class="section-label">Compatibility Matrix</span>
      <span class="text-[10px] text-zinc-600 font-body">{{ models.length }} models, {{ enabledParams.length }} params</span>
    </div>
    <div class="px-5 py-3 overflow-x-auto">
      <table class="w-full text-xs results-table">
        <thead>
          <tr style="border-bottom:1px solid var(--border-subtle);">
            <th class="px-3 py-2 text-left section-label">Model</th>
            <th v-for="param in enabledParams" :key="param" class="px-3 py-2 text-center section-label">
              {{ formatParamName(param) }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="model in models" :key="model.id">
            <td class="px-3 py-2 text-xs font-mono text-zinc-300">{{ model.shortName }}</td>
            <td v-for="param in enabledParams" :key="param" class="px-3 py-2 text-center">
              <span v-if="getStatus(model, param) === 'supported'" class="text-lime-400" title="Supported">
                <svg class="w-3.5 h-3.5 inline" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>
              </span>
              <span v-else-if="getStatus(model, param) === 'clamped'" class="text-yellow-400" :title="'Clamped: ' + getClampNote(model, param)">
                <svg class="w-3.5 h-3.5 inline" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/></svg>
              </span>
              <span v-else-if="getStatus(model, param) === 'dropped'" class="text-red-400" title="Not supported - will be dropped">
                <svg class="w-3.5 h-3.5 inline" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/></svg>
              </span>
              <span v-else class="text-zinc-600" title="Passthrough (unknown)">
                <svg class="w-3.5 h-3.5 inline" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01"/></svg>
              </span>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- Legend -->
      <div class="flex gap-4 mt-3 text-[10px] font-body text-zinc-600">
        <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-lime-400 inline-block"></span> Supported</span>
        <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-yellow-400 inline-block"></span> Clamped</span>
        <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-red-400 inline-block"></span> Dropped</span>
        <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-zinc-600 inline-block"></span> Passthrough</span>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  models: { type: Array, default: () => [] },
  enabledParams: { type: Array, default: () => [] },
  paramSupport: { type: Object, default: null },
  registry: { type: Object, default: null },
})

function formatParamName(name) {
  const names = {
    temperature: 'Temp',
    top_p: 'Top P',
    top_k: 'Top K',
    tool_choice: 'Tool Choice',
    repetition_penalty: 'Rep Pen',
    min_p: 'Min P',
    frequency_penalty: 'Freq Pen',
    presence_penalty: 'Pres Pen',
  }
  return names[name] || name.replace(/_/g, ' ')
}

function getStatus(model, param) {
  const ps = props.paramSupport
  if (!ps || !ps.provider_defaults) return 'passthrough'

  const provPS = ps.provider_defaults[model.rk || model.providerKey]
  if (!provPS) return 'passthrough'

  // Check model-level overrides first
  const modelOverrides = ps.model_overrides?.[model.rk || model.providerKey] || {}
  for (const [pattern, overrideData] of Object.entries(modelOverrides)) {
    if (matchesGlob(model.id, pattern)) {
      const override = overrideData?.params?.[param]
      if (override) {
        if (override.supported === false) return 'dropped'
        return 'supported'
      }
    }
  }

  const pspec = provPS.params?.[param]
  if (!pspec) {
    // Check skip_params
    const skipParams = provPS.skip_params || []
    if (skipParams.includes(param)) return 'dropped'
    return 'passthrough'
  }

  if (pspec.supported === false) return 'dropped'
  if (pspec.clamp) return 'clamped'
  return 'supported'
}

function getClampNote(model, param) {
  const ps = props.paramSupport
  if (!ps || !ps.provider_defaults) return ''
  const provPS = ps.provider_defaults[model.rk || model.providerKey]
  if (!provPS) return ''
  const pspec = provPS.params?.[param]
  if (!pspec || !pspec.clamp) return ''
  return `min: ${pspec.clamp.min ?? '?'}, max: ${pspec.clamp.max ?? '?'}`
}

function matchesGlob(modelId, pattern) {
  return pattern.split('|').some(p => {
    const regex = new RegExp('^' + p.replace(/[.*+?^${}()|[\]\\]/g, m => m === '*' ? '.*' : '\\' + m) + '$', 'i')
    return regex.test(modelId)
  })
}
</script>
