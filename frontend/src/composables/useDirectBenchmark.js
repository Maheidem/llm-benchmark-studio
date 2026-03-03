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
  async function runDirectBenchmark(target, messages, maxTokens, temperature, contextTokens, runNumber = 1, totalRuns = 1, timeoutSec = 300) {
    const controller = abortController.value || createAbortController()
    const timeoutId = setTimeout(() => controller.abort(), timeoutSec * 1000)

    const modelForApi = _stripModelPrefix(target.model_id, target.model_id_prefix || target.model_prefix)
    const addressSpace = _getAddressSpace(target.api_base)

    const baseResult = {
      type: 'result',
      provider: target.provider_key || target.provider_display_name,
      model: target.model_display_name || target.model_id,
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
          output_speed_tps: 0, itl_ms: 0,
          success: false, error: `[http_${response.status}] ${response.statusText}`,
        }
      }

      // Safety check: if server returned non-SSE response (e.g. JSON error),
      // parse it directly instead of attempting SSE stream parsing
      const contentType = response.headers.get('content-type') || ''
      if (!contentType.includes('text/event-stream') && contentType.includes('application/json')) {
        clearTimeout(timeoutId)
        const text = await response.text()
        let errorMsg = '[non_streaming] Server returned JSON instead of SSE stream'
        try {
          const json = JSON.parse(text)
          if (json.error) {
            errorMsg = `[api_error] ${json.error.message || JSON.stringify(json.error)}`
          }
        } catch { /* not parseable */ }
        return {
          ...baseResult,
          ttft_ms: null, total_time_s: (performance.now() - t0) / 1000,
          output_tokens: 0, input_tokens: 0,
          tokens_per_second: 0, input_tokens_per_second: null,
          output_speed_tps: 0, itl_ms: 0,
          success: false, error: errorMsg,
        }
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let lastUsage = null
      let apiError = null

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
            // Detect API errors returned inside SSE events
            if (chunk.error) {
              apiError = chunk.error.message || JSON.stringify(chunk.error)
              continue
            }
            const delta = chunk.choices?.[0]?.delta
            // Handle both standard content AND reasoning model content
            // Reasoning models (Qwen3.5, DeepSeek-R1, etc.) use delta.reasoning_content
            const content = delta?.content
            const reasoning = delta?.reasoning_content
            const hasOutput = content || reasoning
            if (hasOutput && ttft === null) {
              ttft = performance.now() - t0
            }
            if (hasOutput) {
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

      // If an API error was found in the SSE stream, report it
      if (apiError) {
        return {
          ...baseResult,
          ttft_ms: null, total_time_s: Math.round(totalTime * 1000) / 1000,
          output_tokens: 0, input_tokens: lastUsage?.prompt_tokens || 0,
          tokens_per_second: 0, input_tokens_per_second: null,
          output_speed_tps: 0, itl_ms: 0,
          success: false, error: `[api_error] ${apiError}`,
        }
      }

      // Use usage data if available, otherwise use chunk count
      if (lastUsage) {
        if (lastUsage.completion_tokens) outputTokens = lastUsage.completion_tokens
        if (lastUsage.prompt_tokens) inputTokens = lastUsage.prompt_tokens
      }

      // Zero output tokens = model produced nothing (context overflow, empty response, etc.)
      if (outputTokens === 0) {
        console.warn(`[DirectBenchmark] 0 output tokens for ${baseResult.model} @ ${contextTokens}tok context — check model context length in LM Studio`)
        return {
          ...baseResult,
          ttft_ms: null, total_time_s: Math.round(totalTime * 1000) / 1000,
          output_tokens: 0, input_tokens: lastUsage?.prompt_tokens || inputTokens,
          tokens_per_second: 0, input_tokens_per_second: null,
          output_speed_tps: 0, itl_ms: 0,
          success: false,
          error: `[no_output] Model returned 0 tokens — check context length in LM Studio (${contextTokens > 0 ? contextTokens + ' context tokens sent' : 'no context filler'})`,
        }
      }

      const tps = totalTime > 0 ? outputTokens / totalTime : 0

      // Output Speed (excludes TTFT — industry standard)
      const ttftSec = (ttft || 0) / 1000
      const genTime = totalTime - ttftSec
      const outputSpeed = genTime > 0 && outputTokens > 0 ? outputTokens / genTime : 0
      // Inter-Token Latency
      const itl = (outputTokens > 1 && genTime > 0) ? (genTime / (outputTokens - 1)) * 1000 : 0

      return {
        ...baseResult,
        ttft_ms: ttft !== null ? Math.round(ttft * 100) / 100 : null,
        total_time_s: Math.round(totalTime * 1000) / 1000,
        output_tokens: outputTokens,
        input_tokens: inputTokens,
        tokens_per_second: Math.round(tps * 100) / 100,
        input_tokens_per_second: null,
        output_speed_tps: Math.round(outputSpeed * 100) / 100,
        itl_ms: Math.round(itl * 10) / 10,
        success: true,
        error: null,
      }
    } catch (err) {
      clearTimeout(timeoutId)
      let errorMsg = '[network_error] ' + (err.message || 'Unknown error')
      if (err.name === 'AbortError') {
        errorMsg = `[timeout] Request timed out after ${timeoutSec >= 60 ? Math.floor(timeoutSec / 60) + 'm' : timeoutSec + 's'}`
      }
      if (err.message?.includes('Failed to fetch') || err.message?.includes('NetworkError')) {
        errorMsg = '[cors_blocked] Enable CORS in LM Studio: Developer > Local Server > Enable CORS'
      }

      return {
        ...baseResult,
        ttft_ms: null, total_time_s: null, output_tokens: 0, input_tokens: 0,
        tokens_per_second: 0, input_tokens_per_second: null,
        output_speed_tps: 0, itl_ms: 0,
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
