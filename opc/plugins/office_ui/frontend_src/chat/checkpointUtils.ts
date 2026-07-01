import type { ChatMessage, ChatMessageMeta, CheckpointReplyMetadata } from '../types/chat'

const CHECKPOINT_TYPES = new Set([
  'company_work_item_gate',
  'company_delivery_feedback',
  'company_staffing_selection',
  'company_recruitment_confirmation',
  'company_reorg_pending',
  'human_escalation',
  'task_user_input',
])

const TERMINAL_CHECKPOINT_STATUSES = new Set([
  'responded',
  'resolved',
  'timeout',
  'timed_out',
  'expired',
  'stale',
  'superseded',
  'ignored',
  'cancelled',
  'canceled',
  'invalid',
])

export function isCheckpointType(value: string | undefined): boolean {
  return CHECKPOINT_TYPES.has(String(value ?? '').trim())
}

export function isCheckpointCardMetadata(meta: ChatMessageMeta | undefined): boolean {
  if (!isCheckpointType(meta?.checkpoint_type)) {
    return false
  }
  if (meta?.self_evolution_completed) {
    return false
  }
  if (String(meta?.kind ?? '').trim() === 'company_self_evolution_result') {
    return false
  }
  return true
}

export function isCheckpointResolved(meta: ChatMessageMeta | undefined): boolean {
  const status = String(meta?.checkpoint_status ?? '').trim().toLowerCase()
  if (TERMINAL_CHECKPOINT_STATUSES.has(status)) {
    return true
  }
  return !!String(meta?.checkpoint_response_message_id ?? '').trim()
}

export function toCheckpointReplyMetadata(meta: ChatMessageMeta | undefined): CheckpointReplyMetadata | undefined {
  const checkpointId = String(meta?.checkpoint_id ?? '').trim()
  if (!checkpointId) {
    return undefined
  }
  const checkpointType = String(meta?.checkpoint_type ?? '').trim()
  const escalationId = String(meta?.escalation_id ?? '').trim()
  return {
    response_to_checkpoint_id: checkpointId,
    response_to_checkpoint_type: checkpointType || undefined,
    response_to_escalation_id: escalationId || undefined,
  }
}

export function checkpointReplyMetadataForComposer(
  meta: CheckpointReplyMetadata | undefined,
): CheckpointReplyMetadata | undefined {
  const checkpointType = String(meta?.response_to_checkpoint_type ?? '').trim()
  if (checkpointType === 'company_delivery_feedback' || checkpointType === 'human_escalation') {
    return undefined
  }
  return meta
}

export function isResponseForCheckpoint(message: ChatMessage, checkpointMeta: ChatMessageMeta | undefined): boolean {
  if (message.sender !== 'user') {
    return false
  }
  const checkpointId = String(checkpointMeta?.checkpoint_id ?? '').trim()
  if (!checkpointId) {
    return false
  }
  const replyMeta = message.metadata
  if (String(replyMeta?.response_to_checkpoint_id ?? '').trim() === checkpointId) {
    return true
  }
  const checkpointType = String(checkpointMeta?.checkpoint_type ?? '').trim()
  const escalationId = String(checkpointMeta?.escalation_id ?? '').trim()
  return checkpointType === 'human_escalation'
    && !!escalationId
    && String(replyMeta?.response_to_escalation_id ?? '').trim() === escalationId
}

export function analyzeCheckpointMessages(messages: ChatMessage[]): {
  pendingMessageIds: Set<string>
  respondedMessageIds: Set<string>
  duplicateMessageIds: Set<string>
  latestPendingReplyMetadata?: CheckpointReplyMetadata
} {
  const pendingMessageIds = new Set<string>()
  const respondedMessageIds = new Set<string>()
  const duplicateMessageIds = new Set<string>()
  let latestPendingReplyMetadata: CheckpointReplyMetadata | undefined
  const latestCheckpointReplyIndex = new Map<string, number>()
  const latestEscalationReplyIndex = new Map<string, number>()
  const seenCheckpointIds = new Set<string>()

  for (let i = 0; i < messages.length; i++) {
    const message = messages[i]
    if (message.sender !== 'user') continue
    const replyMeta = message.metadata
    const checkpointId = String(replyMeta?.response_to_checkpoint_id ?? '').trim()
    if (checkpointId) {
      latestCheckpointReplyIndex.set(checkpointId, i)
    }
    const escalationId = String(replyMeta?.response_to_escalation_id ?? '').trim()
    if (escalationId) {
      latestEscalationReplyIndex.set(escalationId, i)
    }
  }

  for (let i = 0; i < messages.length; i++) {
    const message = messages[i]
    const checkpointMeta = message.metadata
    if (!isCheckpointCardMetadata(checkpointMeta)) {
      continue
    }

    const checkpointId = String(checkpointMeta?.checkpoint_id ?? '').trim()
    const checkpointType = String(checkpointMeta?.checkpoint_type ?? '').trim()
    const escalationId = String(checkpointMeta?.escalation_id ?? '').trim()
    if (checkpointId && seenCheckpointIds.has(checkpointId)) {
      duplicateMessageIds.add(message.id)
      continue
    }
    if (checkpointId) {
      seenCheckpointIds.add(checkpointId)
    }

    const hasLaterCheckpointReply = !!checkpointId && (latestCheckpointReplyIndex.get(checkpointId) ?? -1) > i
    const hasLaterEscalationReply = checkpointType === 'human_escalation'
      && !!escalationId
      && (latestEscalationReplyIndex.get(escalationId) ?? -1) > i

    if (isCheckpointResolved(checkpointMeta) || hasLaterCheckpointReply || hasLaterEscalationReply) {
      respondedMessageIds.add(message.id)
      continue
    }

    pendingMessageIds.add(message.id)
    latestPendingReplyMetadata = toCheckpointReplyMetadata(checkpointMeta) ?? latestPendingReplyMetadata
  }

  return {
    pendingMessageIds,
    respondedMessageIds,
    duplicateMessageIds,
    latestPendingReplyMetadata,
  }
}
