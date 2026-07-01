import { useState } from 'react'

export interface RecoverableWorkItem {
  work_item_projection_id: string
  title: string
  task_id: string
  status: string
  interrupted: boolean
  previous_status: string
}

export interface InterruptedWorkItemRuntime {
  parent_session_id: string
  parent_task_id: string
  project_id: string
  title: string
  profile: string
  interrupted_at: string
  work_items: RecoverableWorkItem[]
}

export interface RecoveryStatusPayload {
  interrupted: InterruptedWorkItemRuntime[]
  active_recoveries: string[]
  scanned_at: number
}

interface WorkItemRecoveryPanelProps {
  data: RecoveryStatusPayload
  onResume: (parentTaskId: string) => void
  onCancel: (parentTaskId: string) => void
}

const STATUS_ICON: Record<string, string> = {
  done: '\u2713',
  failed: '\u2717',
  pending: '\u25CB',
  blocked: '\u25A0',
  cancelled: '\u2014',
  running: '\u25B6',
}

const STATUS_COLOR: Record<string, string> = {
  done: 'var(--green, #27ae60)',
  failed: 'var(--red, #e74c3c)',
  pending: 'var(--text-secondary, #888)',
  blocked: 'var(--yellow, #f39c12)',
  cancelled: 'var(--text-dim, #555)',
  running: 'var(--accent, #3498db)',
}

export function WorkItemRecoveryPanel({ data, onResume, onCancel }: WorkItemRecoveryPanelProps) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())

  if (!data.interrupted.length && !data.active_recoveries.length) return null

  const visible = data.interrupted.filter(w => !dismissed.has(w.parent_task_id))
  if (!visible.length && !data.active_recoveries.length) return null

  return (
    <div className="wfr-panel">
      {visible.map(wf => {
        const isRecovering = data.active_recoveries.includes(wf.parent_task_id)
        const doneCount = wf.work_items.filter(item => item.status === 'done').length
        const failedCount = wf.work_items.filter(item => item.interrupted || item.status === 'failed').length

        return (
          <div key={wf.parent_task_id} className="wfr-card">
            <div className="wfr-header">
              <span className="wfr-icon">&#x26A0;</span>
              <div className="wfr-header-text">
                <span className="wfr-title">Interrupted: {wf.title}</span>
                <span className="wfr-subtitle">
                  {doneCount}/{wf.work_items.length} work items done, {failedCount} interrupted
                  {wf.profile && <> &middot; {wf.profile}</>}
                </span>
              </div>
            </div>

            <div className="wfr-work-items">
              {wf.work_items.map(item => (
                <div key={item.work_item_projection_id} className={`wfr-work-item wfr-work-item--${item.status}`}>
                  <span className="wfr-work-item-icon" style={{ color: STATUS_COLOR[item.status] || STATUS_COLOR.pending }}>
                    {STATUS_ICON[item.status] || STATUS_ICON.pending}
                  </span>
                  <span className="wfr-work-item-title">{item.title}</span>
                  {item.interrupted && <span className="wfr-work-item-badge">interrupted</span>}
                </div>
              ))}
            </div>

            <div className="wfr-actions">
              {isRecovering ? (
                <span className="wfr-recovering">
                  <span className="spinner-inline" /> Recovering...
                </span>
              ) : (
                <>
                  <button className="wfr-btn wfr-btn--resume" onClick={() => onResume(wf.parent_task_id)}>
                    Resume
                  </button>
                  <button className="wfr-btn wfr-btn--cancel" onClick={() => onCancel(wf.parent_task_id)}>
                    Cancel
                  </button>
                  <button className="wfr-btn wfr-btn--dismiss" onClick={() => setDismissed(prev => new Set(prev).add(wf.parent_task_id))}>
                    Dismiss
                  </button>
                </>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
