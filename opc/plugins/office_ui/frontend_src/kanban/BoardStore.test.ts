import assert from 'node:assert/strict'

/**
 * Regression tests for BoardStore selection behavior.
 *
 * Bug history:
 *   - Original: BoardStore auto-selected boards[0] whenever activeBoardId was
 *     null. Combined with the parent's session-driven clear, this created an
 *     infinite toggle loop (screen flicker).
 *   - Previous fix: limited auto-select to boards.length === 1 — but in
 *     company mode with exactly 1 session (1 board) this STILL flickered
 *     when no session was selected.
 *   - Current fix: BoardStore does NOT auto-select at all. Selection is
 *     entirely driven by the parent (WorkspacePage) which knows the mode.
 *     initFromBackend only clears when the prior selection disappears.
 */

// initFromBackend: only preserve-or-clear, no auto-default
function resolveActiveBoardAfterInit(
  prev: string | null,
  bds: { id: string }[],
): string | null {
  return prev && bds.some(b => b.id === prev) ? prev : null
}

// Parent-driven selection (simulates WorkspacePage useEffect)
function parentChooseBoard(opts: {
  isCompanyMode: boolean
  activeSessionBoardId: string | null
  boards: { id: string }[]
  currentActive: string | null
}): string | null {
  const { isCompanyMode, activeSessionBoardId, boards, currentActive } = opts
  const hasId = (id: string | null) => !!id && boards.some(b => b.id === id)
  if (isCompanyMode) {
    if (activeSessionBoardId && hasId(activeSessionBoardId)) return activeSessionBoardId
    return null
  }
  if (currentActive && hasId(currentActive)) return currentActive
  return boards.length > 0 ? boards[0].id : null
}

// ── initFromBackend ──────────────────────────────────────────────────────

// Single board: do NOT auto-select (parent picks)
assert.strictEqual(
  resolveActiveBoardAfterInit(null, [{ id: 'project-board' }]),
  null,
  'init with null prev stays null even with 1 board',
)

// Preserve valid prior
assert.strictEqual(
  resolveActiveBoardAfterInit('session-a', [{ id: 'session-a' }, { id: 'session-b' }]),
  'session-a',
  'preserves existing active when still present',
)

// Clear stale
assert.strictEqual(
  resolveActiveBoardAfterInit('deleted', [{ id: 'session-a' }]),
  null,
  'clears stale active',
)

// Empty
assert.strictEqual(
  resolveActiveBoardAfterInit(null, []),
  null,
  'empty boards → null',
)

// ── Parent-driven selection ──────────────────────────────────────────────

// Company mode: no session → null (shows empty state)
assert.strictEqual(
  parentChooseBoard({
    isCompanyMode: true,
    activeSessionBoardId: null,
    boards: [{ id: 'session-a' }],
    currentActive: null,
  }),
  null,
  'company mode + no session → null',
)

// Company mode: session selected, its board exists → select it
assert.strictEqual(
  parentChooseBoard({
    isCompanyMode: true,
    activeSessionBoardId: 'session-a',
    boards: [{ id: 'session-a' }, { id: 'session-b' }],
    currentActive: null,
  }),
  'session-a',
  'company mode + session with board → select session board',
)

// Company mode: session selected but its board doesn't exist yet → null
assert.strictEqual(
  parentChooseBoard({
    isCompanyMode: true,
    activeSessionBoardId: 'new-session',
    boards: [{ id: 'session-a' }],
    currentActive: null,
  }),
  null,
  'company mode + new session (no board yet) → null (empty state)',
)

// Non-company mode: auto-select project board
assert.strictEqual(
  parentChooseBoard({
    isCompanyMode: false,
    activeSessionBoardId: null,
    boards: [{ id: 'project-board' }],
    currentActive: null,
  }),
  'project-board',
  'non-company mode → auto-select project board',
)

// ── Flicker regression: 1-session company mode, no session selected ──────
// The original bug: boards.length===1 triggered auto-select, parent cleared
// → loop. With the current contract, BOTH init and parent agree on null.
{
  const boards = [{ id: 'session-a' }]
  const afterInit = resolveActiveBoardAfterInit(null, boards)
  assert.strictEqual(afterInit, null, 'init: null with 1 board stays null')
  const afterParent = parentChooseBoard({
    isCompanyMode: true,
    activeSessionBoardId: null,
    boards,
    currentActive: afterInit,
  })
  assert.strictEqual(afterParent, null, 'parent: 1-session company + no active → null (stable, no loop)')
}

console.log('BoardStore selection contract passed')
