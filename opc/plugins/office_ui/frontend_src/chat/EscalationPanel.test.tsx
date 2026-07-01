import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import { EscalationPanel } from './EscalationPanel'

const markup = renderToStaticMarkup(
  React.createElement(EscalationPanel, {
    meta: {
      checkpoint_type: 'company_work_item_gate',
      checkpoint_id: 'cp-gate',
      prompt: 'Gate Review\n\n- Confirm the artifact exists\n- Confirm tests passed\n\n```json\n{"ok": true}\n```',
      summary: 'Review the gate evidence.',
      options: [
        { id: 'approve', label: 'Approve' },
        { id: 'deny', label: 'Deny' },
      ],
      active_subagents: [{ id: 'sub-1' }],
      worktree_path: '/tmp/work',
    },
    onReply: () => undefined,
    responded: false,
  }),
)

assert.match(markup, /Gate Review/)
assert.match(markup, /<li>Confirm the artifact exists<\/li>/)
assert.match(markup, /<code class="language-json">/)
assert.match(markup, /<summary>Runtime State<\/summary>/)
assert.equal((markup.match(/<button/g) ?? []).length, 3)

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, 'EscalationPanel.tsx'), 'utf8')
assert.doesNotMatch(src, /localResponded|setLocalResponded/, 'panel must wait for server checkpoint metadata before showing responded state')

console.log('EscalationPanel.test.tsx: OK (markdown gate panel)')
