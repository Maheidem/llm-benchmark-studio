import { ref } from 'vue'

export function useDirectBenchmark() {
  const abortController = ref(null)

  /**
   * Feature-detect Chrome Local Network Access (LNA) support.
   * Chrome 142+ (Oct 2025) supports targetAddressSpace in fetch requests.
   */
  function supportsDirectLocalAccess() {
    try {
      new Request('https://test.example', { targetAddressSpace: 'loopback' })
      return true
    } catch {
      return false
    }
  }

  /**
   * Check if a provider's api_base points to a local/LAN address.
   */
  function isLocalProvider(provider) {
    const apiBase = provider?.api_base
    if (!apiBase) return false
    try {
      const url = new URL(apiBase)
      const host = url.hostname.toLowerCase()
      if (host === 'localhost' || host === '127.0.0.1' || host === '::1') return true
      if (host.endsWith('.local')) return true
      // Private IP ranges
      const parts = host.split('.')
      if (parts.length === 4 && parts.every(p => /^\d+$/.test(p))) {
        const [a, b] = parts.map(Number)
        if (a === 10) return true                          // 10.x.x.x
        if (a === 172 && b >= 16 && b <= 31) return true   // 172.16-31.x.x
        if (a === 192 && b === 168) return true             // 192.168.x.x
      }
      return false
    } catch {
      return false
    }
  }

  /**
   * Determine the targetAddressSpace for a given api_base URL.
   */
  function _getAddressSpace(apiBase) {
    try {
      const host = new URL(apiBase).hostname.toLowerCase()
      if (host === 'localhost' || host === '127.0.0.1' || host === '::1') return 'loopback'
      return 'local'
    } catch {
      return 'local'
    }
  }

  /**
   * Strip provider prefix from litellm_id.
   * e.g., "lm_studio/qwen3-coder" with prefix "lm_studio" -> "qwen3-coder"
   */
  function _stripModelPrefix(litellmId, modelPrefix) {
    if (modelPrefix && litellmId.startsWith(modelPrefix + '/')) {
      return litellmId.slice(modelPrefix.length + 1)
    }
    return litellmId
  }

  /**
   * Generate filler text to simulate context tokens.
   * Rough approximation: 1 token ~= 4 characters.
   */
  function generateContextFiller(contextTokens) {
    if (!contextTokens || contextTokens <= 0) return ''
    const targetChars = contextTokens * 4
    const words = 'The quick brown fox jumps over the lazy dog and continues running across the field '
    const repetitions = Math.ceil(targetChars / words.length)
    return words.repeat(repetitions).slice(0, targetChars)
  }

  /**
   * Create a fresh AbortController for cancel support.
   */
  function createAbortController() {
    abortController.value = new AbortController()
    return abortController.value
  }

  /**
   * Cancel any in-progress direct benchmark.
   */
  function cancelDirect() {
    if (abortController.value) {
      abortController.value.abort()
      abortController.value = null
    }
  }

  /**
   * Run a single benchmark against a local LLM directly from the browser.
   * Returns a result object matching the handleSSE() shape.
   */
  async function runDirectBenchmark(target, messages, maxTokens, temperature, contextTokens, runNumber = 1, totalRuns = 1) {
    const controller = abortController.value || createAbortController()
    const timeoutId = setTimeout(() => controller.abort(), 30000) // 30s timeout

    const modelForApi = _stripModelPrefix(target.model_id, target.model_id_prefix || target.model_prefix)
    const addressSpace = _getAddressSpace(target.api_base)

    const baseResult = {
      type: 'result',
      provider: target.provider_key || target.display_name,
      model: target.display_name || target.model_id,
      model_id: target.model_id,
      run: runNumber,
      runs: totalRuns,
      context_tokens: contextTokens || 0,
      cost: 0,
      _direct: true,
    }

    const t0 = performance.now()
    let ttft = null
    let outputTokens = 0
    let inputTokens = 0

    try {
      const fetchOpts = {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: modelForApi,
          messages,
          max_tokens: maxTokens,
          temperature,
          stream: true,
        }),
        signal: controller.signal,
      }

      // Add targetAddressSpace for Chrome LNA
      if (supportsDirectLocalAccess()) {
        fetchOpts.targetAddressSpace = addressSpace
      }

      const response = await fetch(target.api_base + '/chat/completions', fetchOpts)

      if (!response.ok) {
        clearTimeout(timeoutId)
        return {
          ...baseResult,
          ttft_ms: null, total_time_s: null, output_tokens: 0, input_tokens: 0,
          tokens_per_second: 0, input_tokens_per_second: null,
          success: false, error: `[http_${response.status}] ${response.statusText}`,
        }
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let lastUsage = null

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() // keep incomplete line

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed || !trimmed.startsWith('data:')) continue
          const payload = trimmed.slice(5).trim()
          if (payload === '[DONE]') continue

          try {
            const chunk = JSON.parse(payload)
            const content = chunk.choices?.[0]?.delta?.content
            if (content && ttft === null) {
              ttft = performance.now() - t0
            }
            if (content) {
              outputTokens++  // count content chunks as rough token count
            }
            // Capture usage from final chunk if available
            if (chunk.usage) {
              lastUsage = chunk.usage
            }
          } catch {
            // Skip malformed chunks
          }
        }
      }

      clearTimeout(timeoutId)
      const totalTime = (performance.now() - t0) / 1000 // seconds

      // Use usage data if available, otherwise use chunk count
      if (lastUsage) {
        if (lastUsage.completion_tokens) outputTokens = lastUsage.completion_tokens
        if (lastUsage.prompt_tokens) inputTokens = lastUsage.prompt_tokens
      }

      const tps = totalTime > 0 ? outputTokens / totalTime : 0

      return {
        ...baseResult,
        ttft_ms: ttft !== null ? Math.round(ttft * 100) / 100 : null,
        total_time_s: Math.round(totalTime * 1000) / 1000,
        output_tokens: outputTokens,
        input_tokens: inputTokens,
        tokens_per_second: Math.round(tps * 100) / 100,
        input_tokens_per_second: null,
        success: true,
        error: null,
      }
    } catch (err) {
      clearTimeout(timeoutId)
      let errorMsg = '[network_error] ' + (err.message || 'Unknown error')
      if (err.name === 'AbortError') {
        errorMsg = '[timeout] Request timed out after 30s'
      }
      if (err.message?.includes('Failed to fetch') || err.message?.includes('NetworkError')) {
        errorMsg = '[cors_blocked] Enable CORS in LM Studio: Developer > Local Server > Enable CORS'
      }

      return {
        ...baseResult,
        ttft_ms: null, total_time_s: null, output_tokens: 0, input_tokens: 0,
        tokens_per_second: 0, input_tokens_per_second: null,
        success: false, error: errorMsg,
      }
    }
  }

  return {
    supportsDirectLocalAccess,
    isLocalProvider,
    runDirectBenchmark,
    generateContextFiller,
    createAbortController,
    cancelDirect,
    abortController,
  }
}
