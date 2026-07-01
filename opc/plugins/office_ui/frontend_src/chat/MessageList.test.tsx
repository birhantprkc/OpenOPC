import assert from 'node:assert/strict'

import { buildNarrativeMessageItems, copyTextToClipboard, parseProjectUpdatePayload, shouldReleaseStickToBottomOnScroll } from './MessageList'
import type { ChatMessage } from '../types/chat'

assert.equal(
  shouldReleaseStickToBottomOnScroll({
    previousScrollTop: 1200,
    nextScrollTop: 900,
    atBottom: false,
    userScrolling: false,
    programmaticScroll: false,
  }),
  true,
  'scrollbar drag upward should release stick-to-bottom even without wheel/pointer events',
)

assert.equal(
  shouldReleaseStickToBottomOnScroll({
    previousScrollTop: 1200,
    nextScrollTop: 900,
    atBottom: false,
    userScrolling: false,
    programmaticScroll: true,
  }),
  false,
  'programmatic scrolls should not release stick-to-bottom',
)

assert.equal(
  shouldReleaseStickToBottomOnScroll({
    previousScrollTop: 900,
    nextScrollTop: 900,
    atBottom: false,
    userScrolling: true,
    programmaticScroll: false,
  }),
  true,
  'explicit user scroll state should release stick-to-bottom while away from bottom',
)

assert.equal(
  shouldReleaseStickToBottomOnScroll({
    previousScrollTop: 900,
    nextScrollTop: 1200,
    atBottom: true,
    userScrolling: true,
    programmaticScroll: false,
  }),
  false,
  'scrolling back to bottom should keep follow mode available',
)

const parsedUpdate = parseProjectUpdatePayload(JSON.stringify({
  summary: 'Completed the final Chinese memo with source checks.',
  deliverables: [
    { name: 'source_credibility.md', path: '/workspace/source_credibility.md', status: 'complete' },
  ],
  acceptance_status: [
    { criterion: 'Chinese memo', met: true },
    { criterion: 'Citations', met: true },
  ],
  risks: ['Refresh market data after close.'],
  next_actions: ['Use the memo in CEO aggregation.'],
}))
assert.equal(parsedUpdate?.kind, 'report')
assert.equal(parsedUpdate?.deliverables[0]?.name, 'source_credibility.md')
assert.equal(parsedUpdate?.acceptanceSummary, '2/2 acceptance checks met')
assert.deepEqual(parsedUpdate?.risks, ['Refresh market data after close.'])

const prefixedPayload = JSON.stringify({
  summary: 'Focused QA recheck completed.',
  deliverables: [
    { name: 'qa_recheck.md', path: '/workspace/qa_recheck.md', status: 'complete' },
  ],
})
const parsedPrefixedUpdate = parseProjectUpdatePayload(`**Report #1: Recheck remediated screen**: ${prefixedPayload}`)
assert.equal(parsedPrefixedUpdate?.kind, 'report')
assert.equal(parsedPrefixedUpdate?.title, 'Report #1: Recheck remediated screen')
assert.equal(parsedPrefixedUpdate?.summary, 'Focused QA recheck completed.')

const baseMessage = (id: string, content: string, timestamp: number, sender = 'system'): ChatMessage => ({
  id,
  channelId: 'session:root',
  sender,
  senderName: sender === 'user' ? 'You' : 'OPC',
  content,
  timestamp,
  mentions: [],
  metadata: {},
})

const narrativeItems = buildNarrativeMessageItems([
  baseMessage('m1', '[Company:cto::execute::abc] starting Research source reliability', 1000),
  baseMessage('m2', '[Delegating to codex] task=Research source reliability | cmd=codex exec ...', 1100),
  baseMessage('m2b', 'Status digest: Research source reliability', 1150, 'cto'),
  baseMessage('m3', 'The user-visible result is ready.', 1200, 'cto'),
  baseMessage('m4', '[External status] codex started pid=123', 1300),
], { isCompanyRuntime: true, detailMode: 'summary' })

assert.equal(narrativeItems.length, 3)
assert.equal(narrativeItems[0].kind, 'ops-bundle')
assert.equal(narrativeItems[0].kind === 'ops-bundle' ? narrativeItems[0].events.length : 0, 3)
assert.equal(narrativeItems[1].kind, 'message')
assert.equal(narrativeItems[2].kind, 'ops-bundle')

const dedupedProjectUpdates = buildNarrativeMessageItems([
  baseMessage('u1', prefixedPayload, 2000, 'qa_analyst'),
  baseMessage('u2', `**Report #1: Recheck remediated screen**: ${prefixedPayload}`, 2000, 'qa_analyst'),
], { isCompanyRuntime: true, detailMode: 'summary' })
assert.equal(dedupedProjectUpdates.length, 1)
assert.equal(dedupedProjectUpdates[0].kind, 'message')
assert.equal(dedupedProjectUpdates[0].kind === 'message' ? dedupedProjectUpdates[0].msg.id : '', 'u1')

const longResult = 'Completed the focused recheck and produced the QA artifact with caveats for downstream aggregation.'
const dedupedNarrativeMessages = buildNarrativeMessageItems([
  baseMessage('n1', longResult, 3000, 'qa_analyst'),
  baseMessage('n2', `Recheck remediated ten-bagger candidate screen: ${longResult}`, 3000, 'qa_analyst'),
], { isCompanyRuntime: true, detailMode: 'summary' })
assert.equal(dedupedNarrativeMessages.length, 1)
assert.equal(dedupedNarrativeMessages[0].kind === 'message' ? dedupedNarrativeMessages[0].msg.id : '', 'n1')

const duplicatedResultSurface = buildNarrativeMessageItems([
  {
    ...baseMessage('r1', longResult, 4000, 'chao'),
    metadata: { source: 'engine', transcript_kind: 'child_task_result' },
  },
  {
    ...baseMessage('r2', `Deliver final result to user: ${longResult}`, 4500, 'system'),
    senderName: 'Company Member',
    metadata: { source: 'runtime_event', kind: 'worker_notification', notification_kind: 'task_complete' },
  },
], { isCompanyRuntime: true, detailMode: 'summary' })
assert.equal(duplicatedResultSurface.length, 1)
assert.equal(duplicatedResultSurface[0].kind === 'message' ? duplicatedResultSurface[0].msg.id : '', 'r1')

const fullItems = buildNarrativeMessageItems([
  baseMessage('m1', '[Company:cto::execute::abc] starting Research source reliability', 1000),
], { isCompanyRuntime: true, detailMode: 'full' })
assert.equal(fullItems[0].kind, 'message')

const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
const originalDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: {
    clipboard: {
      writeText: async () => {
        throw new Error('clipboard denied')
      },
    },
  },
})

let selectedValue = ''
let appendedNode: any = null
Object.defineProperty(globalThis, 'document', {
  configurable: true,
  value: {
    body: {
      appendChild: (node: any) => {
        appendedNode = node
      },
      removeChild: (node: any) => {
        assert.equal(node, appendedNode)
        appendedNode = null
      },
    },
    createElement: () => ({
      value: '',
      style: {},
      setAttribute: () => {},
      focus: () => {},
      select: function () {
        selectedValue = this.value
      },
      setSelectionRange: () => {},
    }),
    execCommand: (command: string) => command === 'copy',
  },
})
assert.equal(await copyTextToClipboard('fallback copy text'), true)
assert.equal(selectedValue, 'fallback copy text')
assert.equal(appendedNode, null)

if (originalNavigator) {
  Object.defineProperty(globalThis, 'navigator', originalNavigator)
} else {
  delete (globalThis as any).navigator
}
if (originalDocument) {
  Object.defineProperty(globalThis, 'document', originalDocument)
} else {
  delete (globalThis as any).document
}

console.log('MessageList.test.tsx: OK (scroll + narrative timeline helpers)')
