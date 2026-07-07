import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, 'WorkspacePage.tsx'), 'utf8')

assert.match(src, /makeOptimisticUserMessageId/, 'ordinary composer sends must create a stable optimistic ui_message_id')
assert.match(src, /chatStore\.sendMessage/, 'ordinary composer sends must echo the user message locally before backend response')
assert.match(src, /ui_message_id: uiMessageId/, 'optimistic local message and websocket metadata must share ui_message_id')
assert.match(src, /checkpointReplyId/, 'checkpoint replies must be excluded from ordinary optimistic composer echo')
assert.match(
  src,
  /const outgoing = metadata\?\.ui_message_id\s*\?\s*metadata\s*:\s*\{ \.\.\.\(metadata \?\? \{\}\), ui_message_id: makeOptimisticUserMessageId\(\) \}/,
  'every session send must carry a client-generated ui_message_id so the backend can deduplicate re-deliveries',
)

console.log('WorkspacePage.test.ts: OK (optimistic composer echo wiring)')
