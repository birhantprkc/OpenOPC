import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import { WorkItemProgressCard } from './WorkItemProgressCard'
import type { RoleWorkItemSummary } from '../types/kanban'

const currentOwnerRoleWorkItems: Record<string, RoleWorkItemSummary> = {
  cto: {
    roleKey: 'cto',
    roleId: 'cto',
    roleName: 'CTO',
    runtimeStatus: 'idle',
    aggregatedStatus: 'waiting',
    workItems: [
      {
        workItemId: 'wi-review',
        phase: 'awaiting_manager_review',
        kanbanColumn: 'in-review',
        title: 'Implement summary',
        kind: 'execute',
        isReviewTarget: true,
        executorRoleId: 'engineer',
        reviewerRoleId: 'cto',
        createdAt: 10,
        updatedAt: 20,
        executionTurnId: 'runtime-task-1',
        progressLog: [],
      },
    ],
  },
}

const executorRoleWorkItems: Record<string, RoleWorkItemSummary> = {
  engineer: {
    roleKey: 'engineer',
    roleId: 'engineer',
    roleName: 'Engineer',
    runtimeStatus: 'idle',
    aggregatedStatus: 'waiting',
    workItems: [
      {
        workItemId: 'wi-review',
        phase: 'awaiting_manager_review',
        kanbanColumn: 'in-review',
        title: 'Implement summary',
        kind: 'execute',
        isReviewTarget: true,
        executorRoleId: 'engineer',
        reviewerRoleId: 'cto',
        createdAt: 10,
        updatedAt: 20,
        executionTurnId: 'runtime-task-1',
        progressLog: [],
      },
    ],
  },
}

const executorMarkup = renderToStaticMarkup(
  React.createElement(WorkItemProgressCard, {
    workItemLog: [],
    roleWorkItems: currentOwnerRoleWorkItems,
    executorRoleWorkItems,
    isCompanyRuntime: true,
  }),
)

assert.match(executorMarkup, /Execution Progress/)
assert.match(executorMarkup, /Engineer/)
assert.doesNotMatch(executorMarkup, /CTO/)

const fallbackMarkup = renderToStaticMarkup(
  React.createElement(WorkItemProgressCard, {
    workItemLog: [],
    roleWorkItems: currentOwnerRoleWorkItems,
    isCompanyRuntime: true,
  }),
)

assert.match(fallbackMarkup, /CTO/)
assert.doesNotMatch(fallbackMarkup, /Engineer/)

console.log('WorkItemProgressCard.test.tsx: OK (executor rollup preferred with current-owner fallback)')
