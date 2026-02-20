/**
 * Shared helper/utility functions extracted from the monolithic index.html.
 */

/**
 * Escape HTML entities to prevent XSS.
 * @param {string} str
 * @returns {string}
 */
export function escapeHtml(str) {
  const div = document.createElement('div')
  div.textContent = str
  return div.innerHTML
}

/**
 * Format a context size (token count) for display.
 * @param {number} tokens
 * @returns {string}
 */
export function formatCtxSize(tokens) {
  if (tokens >= 1000000) return (tokens / 1000000).toFixed(0) + 'M ctx'
  if (tokens >= 1000) return (tokens / 1000).toFixed(0) + 'K ctx'
  return tokens + ' ctx'
}

/**
 * Format a number with locale separators.
 * @param {number} n
 * @returns {string}
 */
export function formatNumber(n) {
  if (n == null) return '-'
  return Number(n).toLocaleString()
}

/**
 * Format a duration in seconds to a human-readable string.
 * @param {number} seconds
 * @returns {string}
 */
export function formatDuration(seconds) {
  if (seconds == null || seconds < 0) return '-'
  if (seconds < 60) return seconds.toFixed(1) + 's'
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  if (mins < 60) return `${mins}m ${secs}s`
  const hrs = Math.floor(mins / 60)
  const remainMins = mins % 60
  return `${hrs}h ${remainMins}m`
}

/**
 * Create a safe HTML id from an arbitrary string.
 * @param {string} str
 * @returns {string}
 */
export function safeId(str) {
  return str.replace(/[^a-zA-Z0-9_-]/g, '_')
}

/**
 * Slugify a string (lowercase, underscores).
 * @param {string} str
 * @returns {string}
 */
export function slugify(str) {
  return str.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '')
}

/**
 * Human-readable relative time string.
 * @param {string} isoStr - ISO 8601 date string
 * @returns {string}
 */
export function timeAgo(isoStr) {
  if (!isoStr) return ''
  const diff = Date.now() - new Date(isoStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return mins + 'm ago'
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return hrs + 'h ago'
  const days = Math.floor(hrs / 24)
  return days + 'd ago'
}
