import assert from 'node:assert/strict'
import type { ChatMessage } from '../types/chat'
import type { Session } from '../types/kanban'
import { mapBackendSession } from './collabSync'
import { canonicalizeSessionExecutionIdentity } from './sessionIdentity'
import { deriveCompanyRuntimeDisplayStatus, getConversationHeaderSession, getConversationSessionView, getWorkItemChildSessions, getWorkItemRoleSessions, mergeConversationMessages, projectSessionConversation } from './workItemSessions'

function makeSession(overrides: Partial<Session> & Pick<Session, 'taskId' | 'channelId' | 'title' | 'status' | 'columnId' | 'assigneeIds' | 'priority' | 'tags' | 'progressLog' | 'createdAt' | 'updatedAt' | 'messageCount'>): Session {
  return {
    projectId: 'test-project',
    ...overrides,
  }
}

const parent = makeSession({
  taskId: 'root-task',
  channelId: 'session:root-task',
  title: 'Root',
  status: 'running',
  columnId: 'in-progress',
  assigneeIds: [],
  priority: null,
  tags: [],
  progressLog: [],
  createdAt: 1,
  updatedAt: 10,
  messageCount: 1,
  mode: 'primary',
  originTaskId: 'root-task',
})

const child = makeSession({
  taskId: 'child-task',
  channelId: 'session:child-task',
  title: 'CEO Intake',
  status: 'done',
  columnId: 'done',
  assigneeIds: ['ceo'],
  priority: null,
  tags: [],
  progressLog: [],
  createdAt: 2,
  updatedAt: 11,
  messageCount: 2,
  mode: 'child',
  parentSessionId: 'parent-session-id-that-has-not-arrived-yet',
  originTaskId: 'root-task',
})

const matches = getWorkItemChildSessions(parent, [parent, child])
assert.equal(matches.length, 1)
assert.equal(matches[0]?.taskId, 'child-task')

const companyParent = makeSession({
  taskId: 'company-root',
  channelId: 'session:company-root',
  sessionId: 'company-root-session',
  title: 'Work-Item Runtime Root',
  status: 'running',
  columnId: 'in-progress',
  assigneeIds: [],
  priority: null,
  tags: [],
  progressLog: [],
  createdAt: 1,
  updatedAt: 10,
  messageCount: 1,
  mode: 'primary',
  execMode: 'company',
  originTaskId: 'company-root',
})

const companyChild = makeSession({
  taskId: 'company-child',
  channelId: 'session:company-child',
  title: 'CTO Delegation',
  status: 'running',
  columnId: 'in-progress',
  assigneeIds: ['cto'],
  priority: null,
  tags: [],
  progressLog: [],
  createdAt: 2,
  updatedAt: 25,
  messageCount: 4,
  mode: 'child',
  parentSessionId: 'company-root-session',
  originTaskId: 'company-root',
})

const companyProjection = projectSessionConversation(companyParent, [companyChild])
assert.deepEqual(
  companyProjection.timelineSessions.map((session) => session.taskId),
  ['company-root', 'company-child'],
)
assert.equal(companyProjection.displaySession?.taskId, 'company-root')
assert.equal(companyProjection.runtimeSession?.taskId, 'company-child')
assert.equal(companyProjection.projectedFromChild, false)

const idleChildWithNewerInboxTimestamp = makeSession({
  ...companyChild,
  taskId: 'company-idle-child',
  channelId: 'session:company-idle-child',
  title: 'Idle Child Inbox Update',
  updatedAt: 1_000_000,
  messageCount: 0,
  progressLog: [],
})
const stableCompanyProjection = projectSessionConversation(companyParent, [idleChildWithNewerInboxTimestamp])
assert.equal(stableCompanyProjection.runtimeSession?.taskId, 'company-root')

const companyHeaderView = getConversationHeaderSession(
  {
    ...companyParent,
    workItemRoleName: 'CEO',
    employeeAssignment: {
      name: 'Root Employee',
      category: 'leadership',
    },
  },
  {
    ...companyChild,
    workItemRoleName: 'CTO',
    contextTokens: 0,
    contextWindow: 128000,
    inputTokens: 11,
    outputTokens: 22,
    totalTokens: 33,
    turnCostUsd: 0.001,
    sessionCostUsd: 0.002,
    selectedExecutionAgent: 'codex',
    employeeAssignment: {
      name: 'Child Employee',
      category: 'engineering',
    },
  },
  [companyParent, companyChild],
)
assert.equal(companyHeaderView?.taskId, 'company-root')
assert.equal(companyHeaderView?.workItemRoleName, 'CEO')
assert.equal(companyHeaderView?.employeeAssignment?.name, 'Root Employee')

const resultMessage = (id: string, channelId: string, content: string, metadata: ChatMessage['metadata'], sender = 'chao'): ChatMessage => ({
  id,
  channelId,
  sender,
  senderName: sender === 'system' ? 'Company Member' : 'Chao',
  content,
  timestamp: 1000 + id.length,
  mentions: [],
  metadata,
})

const finalBody = 'Final delivery is ready with a long enough body to be considered the same user-visible result across transcript mirrors and worker notifications.'
const mergedDeliveryMessages = mergeConversationMessages([
  [
    resultMessage(
      'opc-top-level',
      'session:company-root',
      finalBody,
      { source: 'engine', transcript_kind: 'top_level_reply' },
      'assistant',
    ),
  ],
  [
    resultMessage(
      'parent-mirror',
      'session:company-root',
      `**Deliver final result to user: Chao Intake**: ${finalBody}`,
      { source: 'engine', transcript_kind: 'child_result' },
    ),
  ],
  [
    resultMessage(
      'child-direct',
      'session:company-child',
      finalBody,
      { source: 'engine', transcript_kind: 'child_task_result' },
    ),
    resultMessage(
      'worker-note',
      'session:company-child',
      finalBody,
      { source: 'runtime_event', kind: 'worker_notification', notification_kind: 'task_complete' },
      'system',
    ),
  ],
])

assert.equal(mergedDeliveryMessages.length, 1)
assert.equal(mergedDeliveryMessages[0]?.id, 'child-direct')
assert.equal(companyHeaderView?.status, 'running')
assert.equal(companyHeaderView?.contextTokens, 0)
assert.equal(companyHeaderView?.contextWindow, 128000)
assert.equal(companyHeaderView?.inputTokens, 11)
assert.equal(companyHeaderView?.outputTokens, 22)
assert.equal(companyHeaderView?.totalTokens, 33)
assert.equal(companyHeaderView?.turnCostUsd, 0.001)
assert.equal(companyHeaderView?.sessionCostUsd, 0.002)
assert.equal(companyHeaderView?.selectedExecutionAgent, 'codex')

const customOrgRoot = makeSession({
  taskId: 'custom-root',
  channelId: 'session:custom-root',
  sessionId: 'custom-root-session',
  title: 'Custom Work-Item Runtime Root',
  status: 'running',
  columnId: 'in-progress',
  assigneeIds: ['chief_architect'],
  priority: null,
  tags: [],
  progressLog: [],
  createdAt: 1,
  updatedAt: 30,
  messageCount: 1,
  mode: 'primary',
  execMode: 'custom',
  isCompanyRuntime: true,
  originTaskId: 'custom-root',
  workItemRoleId: 'chief_architect',
  workItemRoleName: 'Chief Architect',
})

const customOrgChild = makeSession({
  taskId: 'custom-child',
  channelId: 'session:custom-child',
  title: 'Research Lead Turn',
  status: 'running',
  columnId: 'in-progress',
  assigneeIds: ['research_lead'],
  priority: null,
  tags: [],
  progressLog: [],
  createdAt: 2,
  updatedAt: 31,
  messageCount: 2,
  mode: 'child',
  parentSessionId: 'custom-root-session',
  originTaskId: 'custom-root',
  workItemRoleId: 'research_lead',
  workItemRoleName: 'Research Lead',
})

assert.deepEqual(
  getWorkItemChildSessions(customOrgRoot, [customOrgRoot, customOrgChild]).map((session) => session.taskId),
  ['custom-child'],
)
assert.deepEqual(
  getWorkItemRoleSessions(customOrgRoot, [customOrgRoot, customOrgChild]).map((session) => session.workItemRoleName),
  ['Chief Architect', 'Research Lead'],
)

const mergedCustomView = getConversationSessionView(
  {
    ...customOrgRoot,
    status: 'failed',
    roleWorkItems: {
      chief_architect: {
        roleKey: 'chief_architect',
        roleId: 'chief_architect',
        roleName: 'Chief Architect',
        runtimeStatus: 'idle',
        aggregatedStatus: 'active',
        workItems: [],
      },
    },
  },
  {
    ...customOrgChild,
    runtimeControlState: 'running',
    canStop: true,
  },
  [customOrgRoot, customOrgChild],
)
assert.equal(mergedCustomView?.runtimeControlState, 'running')
assert.equal(mergedCustomView?.canStop, true)
assert.equal(mergedCustomView?.roleWorkItems?.chief_architect.roleName, 'Chief Architect')
assert.equal(mergedCustomView?.status, 'running')
assert.equal(deriveCompanyRuntimeDisplayStatus(mergedCustomView), 'running')

const failedCustomView = getConversationSessionView(
  {
    ...customOrgRoot,
    status: 'failed',
    roleWorkItems: {
      chief_architect: {
        roleKey: 'chief_architect',
        roleId: 'chief_architect',
        roleName: 'Chief Architect',
        runtimeStatus: 'idle',
        aggregatedStatus: 'failed',
        workItems: [],
      },
    },
  },
  null,
  [],
)
assert.equal(failedCustomView?.status, 'failed')

const runtimeRollupOverridesStaleActiveView = getConversationSessionView(
  {
    ...customOrgRoot,
    status: 'failed',
    roleWorkItems: {
      chief_architect: {
        roleKey: 'chief_architect',
        roleId: 'chief_architect',
        roleName: 'Chief Architect',
        runtimeStatus: 'idle',
        aggregatedStatus: 'failed',
        workItems: [],
      },
    },
  },
  {
    ...customOrgRoot,
    status: 'failed',
    roleWorkItems: {
      chief_architect: {
        roleKey: 'chief_architect',
        roleId: 'chief_architect',
        roleName: 'Chief Architect',
        runtimeStatus: 'tool_active',
        aggregatedStatus: 'active',
        workItems: [],
      },
    },
  },
  [],
)
assert.equal(runtimeRollupOverridesStaleActiveView?.status, 'running')

const companyIdentity = canonicalizeSessionExecutionIdentity({
  taskId: 'company-stale-org',
  execMode: 'company',
  companyProfile: 'custom',
  orgId: 'quantum_harbor',
})
assert.equal(companyIdentity.execMode, 'company')
assert.equal(companyIdentity.companyProfile, 'corporate')
assert.equal(companyIdentity.orgId, undefined)

const customIdentity = canonicalizeSessionExecutionIdentity({
  taskId: 'custom-org',
  execMode: 'org',
  companyProfile: 'corporate',
  orgId: 'quantum_harbor',
})
assert.equal(customIdentity.execMode, 'org')
assert.equal(customIdentity.companyProfile, 'custom')
assert.equal(customIdentity.orgId, 'quantum_harbor')

const mappedCompanySession = mapBackendSession({
  task_id: 'mapped-company',
  channel_id: 'session:mapped-company',
  title: 'Mapped Company',
  status: 'running',
  column_id: 'in-progress',
  assignee_ids: [],
  tags: [],
  created_at: 1,
  updated_at: 2,
  exec_mode: 'company',
  company_profile: 'custom',
  org_id: 'quantum_harbor',
})
assert.equal(mappedCompanySession.execMode, 'company')
assert.equal(mappedCompanySession.companyProfile, 'corporate')
assert.equal(mappedCompanySession.orgId, undefined)

console.log('workItemSessions origin-task linking checks passed')
