/**
 * Shared progress tracking with ETA calculation.
 * Used by paramTuner, promptTuner, and benchmark stores.
 */
export function useActiveSession() {
  let startTime = null
  let stepTimes = []
  const MAX_SAMPLES = 20

  function startTracking() {
    startTime = Date.now()
    stepTimes = []
  }

  function recordStep() {
    stepTimes.push(Date.now())
    if (stepTimes.length > MAX_SAMPLES) {
      stepTimes = stepTimes.slice(-MAX_SAMPLES)
    }
  }

  function calculateETA(completed, total) {
    if (!startTime || completed < 1 || !total || completed >= total) return ''

    const remaining = total - completed

    // Use moving average of recent step durations for smoothed ETA
    if (stepTimes.length >= 2) {
      const recentDurations = []
      for (let i = 1; i < stepTimes.length; i++) {
        recentDurations.push(stepTimes[i] - stepTimes[i - 1])
      }
      const avgStepMs = recentDurations.reduce((s, d) => s + d, 0) / recentDurations.length
      return formatDuration(remaining * avgStepMs)
    }

    // Fallback: linear projection from start
    const elapsed = Date.now() - startTime
    const avgPerStep = elapsed / completed
    return formatDuration(remaining * avgPerStep)
  }

  function formatDuration(ms) {
    if (ms <= 0) return ''
    const seconds = Math.round(ms / 1000)
    if (seconds < 60) return `~${seconds}s left`
    const minutes = Math.round(seconds / 60)
    if (minutes < 60) return `~${minutes}m left`
    const hours = Math.round(minutes / 60)
    return `~${hours}h left`
  }

  function resetTracking() {
    startTime = null
    stepTimes = []
  }

  return {
    startTracking,
    recordStep,
    calculateETA,
    resetTracking,
  }
}
