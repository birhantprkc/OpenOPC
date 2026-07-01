import assert from 'node:assert/strict'
import type { Session } from '../types/kanban'
import { composerExecModeForSession } from './ContextPanel'

function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    projectId: 'default',
    taskId: 'task-1',
    channelId: 'session:task-1',
    title: 'Task',
    status: 'running',
    columnId: 'in-progress',
    assigneeIds: [],
    priority: null,
    tags: [],
    progressLog: [],
    createdAt: 1,
    updatedAt: 2,
    messageCount: 1,
    mode: 'primary',
    ...overrides,
  }
}

assert.equal(
  composerExecModeForSession(makeSession({
    execMode: 'task',
    companyProfile: 'corporate',
    isCompanyRuntime: true,
    workItemProjectionId: 'stale-company-marker',
  }), 'company'),
  'task',
)

assert.equal(
  composerExecModeForSession(makeSession({
    isCompanyRuntime: true,
    workItemProjectionId: 'legacy-company-marker',
  }), 'task'),
  'company',
)

assert.equal(
  composerExecModeForSession(makeSession({
    execMode: 'org',
    companyProfile: 'custom',
    orgId: 'quantum_harbor',
  }), 'task'),
  'org',
)

console.log('ContextPanel composer identity checks passed')
