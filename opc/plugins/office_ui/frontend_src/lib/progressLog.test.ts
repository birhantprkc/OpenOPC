import assert from 'node:assert/strict'
import { appendProgressEntry } from './progressLog'

let log = appendProgressEntry([], {
  timestamp: 1,
  type: 'thinking',
  summary: 'Thinking',
  detail: '我先',
  turnId: 'rt-1:1',
  itemId: 'rt-1:1:thinking',
  seq: 1,
})

for (const [seq, detail] of [
  [2, '先联网'],
  [3, '联网抓'],
  [4, '抓取'],
] as const) {
  log = appendProgressEntry(log, {
    timestamp: seq,
    type: 'thinking',
    summary: 'Thinking',
    detail,
    turnId: 'rt-1:1',
    itemId: 'rt-1:1:thinking',
    seq,
  })
}

assert.equal(log.length, 1)
assert.equal(log[0]?.summary, 'Thinking')
assert.equal(log[0]?.detail, '我先联网抓取')

const unchanged = appendProgressEntry(log, {
  timestamp: 5,
  type: 'thinking',
  summary: 'Thinking',
  detail: '重复',
  turnId: 'rt-1:1',
  itemId: 'rt-1:1:thinking',
  seq: 4,
})

assert.equal(unchanged[0]?.detail, '我先联网抓取')

let toolLog = appendProgressEntry([], {
  timestamp: 10,
  type: 'tool_call',
  summary: 'web_search',
  detail: '{"query":"weather"}',
  turnId: 'rt-1:2',
  toolCallId: 'call-1',
})

toolLog = appendProgressEntry(toolLog, {
  timestamp: 11,
  type: 'tool_call',
  summary: 'web_search',
  detail: 'completed',
  turnId: 'rt-1:2',
  toolCallId: 'call-1',
})

assert.equal(toolLog.length, 1)
assert.equal(toolLog[0]?.detail, '{"query":"weather"}\ncompleted')

let permissionLog = appendProgressEntry([], {
  timestamp: 20,
  type: 'autonomy',
  summary: 'shell_exec: ask',
  turnId: 'rt-1:3',
  permissionGroupKey: 'tool:shell_exec/python:domain:example.com',
})

permissionLog = appendProgressEntry(permissionLog, {
  timestamp: 21,
  type: 'autonomy',
  summary: 'shell_exec: allow',
  turnId: 'rt-1:3',
  permissionGroupKey: 'tool:shell_exec/python:domain:example.com',
})

assert.equal(permissionLog.length, 1)
assert.equal(permissionLog[0]?.summary, 'shell_exec: allow')
