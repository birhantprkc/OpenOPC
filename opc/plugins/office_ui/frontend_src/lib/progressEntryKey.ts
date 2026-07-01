import type { ProgressEntry } from '../types/kanban'

function compact(value: unknown): string {
  return String(value ?? '')
    .trim()
    .replace(/\s+/g, ' ')
    .slice(0, 96)
}

export function progressEntryKey(entry: ProgressEntry, fallbackIndex = 0): string {
  const stableId = entry.itemId || entry.streamId || entry.toolCallId || entry.permissionGroupKey
  if (stableId) {
    return `${entry.type}:${compact(entry.turnId)}:${compact(stableId)}`
  }

  if (entry.type === 'thinking') {
    return `thinking:${compact(entry.turnId) || compact(entry.executionMode) || compact(entry.summary) || 'stream'}:${fallbackIndex}`
  }

  if (entry.type === 'tool_call' && entry.turnId) {
    return `tool:${compact(entry.turnId)}:${compact(entry.summary) || 'tool'}:${fallbackIndex}`
  }

  if (entry.turnId && typeof entry.seq === 'number') {
    return `${entry.type}:${compact(entry.turnId)}:seq:${entry.seq}`
  }

  return [
    entry.type,
    compact(entry.turnId),
    Number.isFinite(entry.timestamp) ? entry.timestamp : '',
    compact(entry.summary),
    compact(entry.detail),
    fallbackIndex,
  ].join(':')
}
