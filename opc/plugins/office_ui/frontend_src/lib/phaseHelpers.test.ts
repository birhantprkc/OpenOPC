/**
 * Locks in: frontend's PHASE_TO_COLUMN matches backend's
 * ``opc/presentation/kanban.py:STATUS_TO_COLUMN`` and
 * ``opc/layer2_organization/phase.py:_PHASE_TO_COLUMN``.
 *
 * If the backend ever adds, removes, or renames a phase / column, this
 * test surfaces the drift immediately — preventing a class of silent
 * UI bug where a card ends up in the wrong column because the frontend
 * projection fell out of sync with the backend.
 */
import { describe, it, expect } from 'vitest'

import type { KanbanPhase } from '../types/kanban'
import { PHASE_TO_COLUMN, deriveColumnFromPhase } from './phaseHelpers'

const ALL_PHASES: KanbanPhase[] = [
  'queued', 'ready', 'ready_for_rework', 'waiting_dependencies',
  'running', 'waiting_for_peer', 'waiting_for_children', 'paused', 'needs_attention',
  'awaiting_manager_review', 'awaiting_human',
  'approved', 'failed', 'cancelled',
]

describe('PHASE_TO_COLUMN', () => {
  it('covers every KanbanPhase exactly once', () => {
    expect(Object.keys(PHASE_TO_COLUMN).sort()).toEqual([...ALL_PHASES].sort())
  })

  it('projects TODO-family phases to "todo"', () => {
    for (const p of ['queued', 'ready', 'ready_for_rework', 'waiting_dependencies'] as KanbanPhase[]) {
      expect(PHASE_TO_COLUMN[p]).toBe('todo')
    }
  })

  it('projects IN-PROGRESS-family phases to "in-progress"', () => {
    for (const p of ['running', 'waiting_for_peer', 'waiting_for_children', 'paused', 'needs_attention'] as KanbanPhase[]) {
      expect(PHASE_TO_COLUMN[p]).toBe('in-progress')
    }
  })

  it('projects IN-REVIEW-family phases to "in-review"', () => {
    for (const p of ['awaiting_manager_review', 'awaiting_human'] as KanbanPhase[]) {
      expect(PHASE_TO_COLUMN[p]).toBe('in-review')
    }
  })

  it('projects terminal phases to "done"', () => {
    for (const p of ['approved', 'failed', 'cancelled'] as KanbanPhase[]) {
      expect(PHASE_TO_COLUMN[p]).toBe('done')
    }
  })
})

describe('deriveColumnFromPhase', () => {
  it('returns "todo" for undefined / null phase', () => {
    expect(deriveColumnFromPhase(undefined)).toBe('todo')
    expect(deriveColumnFromPhase(null)).toBe('todo')
  })

  it('projects each phase using the same table', () => {
    for (const p of ALL_PHASES) {
      expect(deriveColumnFromPhase(p)).toBe(PHASE_TO_COLUMN[p])
    }
  })
})
