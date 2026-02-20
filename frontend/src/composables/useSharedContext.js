import { useToolEvalStore } from '../stores/toolEval.js'

export function useSharedContext() {
  const store = useToolEvalStore()

  function setSuite(suiteId, suiteName) {
    store.setSuite(suiteId, suiteName)
  }

  function setModels(models) {
    store.setModels(models)
  }

  function setSystemPrompt(key, value) {
    store.setSystemPrompt(key, value)
  }

  function clearContext() {
    store.clearContext()
  }

  function setExperiment(id, name) {
    store.sharedContext.experimentId = id
    store.sharedContext.experimentName = name || null
    store.saveContext()
  }

  function setConfig(overrides) {
    Object.assign(store.sharedContext, overrides)
    store.saveContext()
  }

  return {
    context: store.sharedContext,
    setSuite,
    setModels,
    setSystemPrompt,
    clearContext,
    setExperiment,
    setConfig,
  }
}
