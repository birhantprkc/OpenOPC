import assert from 'node:assert/strict'

import type { ChatMessage } from '../types/chat'
import { mapBackendMessage } from '../lib/collabSync'
import { analyzeCheckpointMessages } from './checkpointUtils'
import { __chatStoreTestUtils } from './ChatStore'

const syntheticCheckpoint: ChatMessage = {
  id: 'checkpoint::cp-delivery',
  channelId: 'session:task-1',
  sender: 'assistant',
  senderName: 'Company Member',
  content: 'Human review requested.',
  timestamp: 1,
  mentions: [],
  metadata: {
    checkpoint_type: 'company_delivery_feedback',
    checkpoint_id: 'cp-delivery',
    summary: 'Pending review',
  },
}

const backendCheckpointUpdate: ChatMessage = {
  id: 'db-message-1',
  channelId: 'session:task-1',
  sender: 'assistant',
  senderName: 'Company Member',
  content: 'Human review requested.',
  timestamp: 2,
  mentions: [],
  metadata: {
    checkpoint_type: 'company_delivery_feedback',
    checkpoint_id: 'cp-delivery',
    checkpoint_status: 'ignored',
    checkpoint_reply_kind: 'ignore',
  },
}

const mergedCheckpoint = __chatStoreTestUtils.dedupeMessages([
  syntheticCheckpoint,
  backendCheckpointUpdate,
])

assert.equal(mergedCheckpoint.length, 1)
assert.equal(mergedCheckpoint[0].id, 'db-message-1')
assert.equal(mergedCheckpoint[0].metadata?.checkpoint_status, 'ignored')
assert.deepEqual([...analyzeCheckpointMessages(mergedCheckpoint).pendingMessageIds], [])
assert.deepEqual([...analyzeCheckpointMessages(mergedCheckpoint).respondedMessageIds], ['db-message-1'])

const terminalSyntheticCheckpoint: ChatMessage = {
  ...syntheticCheckpoint,
  timestamp: 2,
  metadata: {
    ...syntheticCheckpoint.metadata,
    checkpoint_status: 'ignored',
    checkpoint_reply_kind: 'ignore',
  },
}

const mergedSameIdCheckpoint = __chatStoreTestUtils.dedupeMessages([
  syntheticCheckpoint,
  terminalSyntheticCheckpoint,
])

assert.equal(mergedSameIdCheckpoint.length, 1)
assert.equal(mergedSameIdCheckpoint[0].id, 'checkpoint::cp-delivery')
assert.equal(mergedSameIdCheckpoint[0].metadata?.checkpoint_status, 'ignored')
assert.deepEqual([...analyzeCheckpointMessages(mergedSameIdCheckpoint).pendingMessageIds], [])

const optimisticUserMessage: ChatMessage = {
  id: 'msg-local',
  channelId: 'session:task-1',
  sender: 'user',
  senderName: 'You',
  content: 'New requirement',
  timestamp: 3,
  mentions: [],
  metadata: {
    ui_message_id: 'ui-1',
  },
}

const backendUserMessage: ChatMessage = {
  id: 'db-user-1',
  channelId: 'session:task-1',
  sender: 'user',
  senderName: 'You',
  content: 'New requirement',
  timestamp: 4,
  mentions: [],
  metadata: {
    ui_message_id: 'ui-1',
  },
}

const mergedUserMessage = __chatStoreTestUtils.dedupeMessages([
  optimisticUserMessage,
  backendUserMessage,
])

assert.equal(mergedUserMessage.length, 1)
assert.equal(mergedUserMessage[0].metadata?.ui_message_id, 'ui-1')

const nativeCompanyRawTurn: ChatMessage = {
  id: 'native-raw-1',
  channelId: 'session:task-1',
  sender: 'assistant',
  senderName: 'Task Generalist',
  content: '最终分析已经完成，结论如下。',
  timestamp: 5,
  mentions: [],
  metadata: {
    source: 'engine',
    transcript_kind: 'runtime_v2_assistant',
  },
}

const companyRoleResult: ChatMessage = {
  id: 'role-result-1',
  channelId: 'session:task-1',
  sender: 'chao',
  senderName: 'Chao',
  content: '最终分析已经完成，结论如下。',
  timestamp: 6,
  mentions: [],
  metadata: {
    source: 'engine',
    transcript_kind: 'company_role_result',
  },
}

const mergedNativeCompanyDuplicate = __chatStoreTestUtils.dedupeMessages([
  nativeCompanyRawTurn,
  companyRoleResult,
])

assert.equal(mergedNativeCompanyDuplicate.length, 1)
assert.equal(mergedNativeCompanyDuplicate[0].id, 'role-result-1')
assert.equal(mergedNativeCompanyDuplicate[0].senderName, 'Chao')

const mappedTaskGeneralistMessage = mapBackendMessage({
  message_id: 'legacy-task-generalist',
  channel_id: 'session:task-1',
  sender: 'task_generalist',
  sender_name: 'Task Generalist',
  content: 'Legacy native task result.',
  created_at: 10,
  metadata: {
    transcript_kind: 'runtime_v2_company_assistant',
  },
})

assert.equal(mappedTaskGeneralistMessage.senderName, 'OPC')

console.log('ChatStore.test.ts: OK (optimistic, checkpoint, and company result identity merging)')
