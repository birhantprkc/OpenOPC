import React, { useCallback, useEffect, useMemo, useState } from 'react'
import type { ChatMessageMeta, HumanEscalationOption } from '../types/chat'
import { MarkdownBody } from './MarkdownBody'

// If the server never confirms (click lost on a dropped connection), re-enable
// the buttons after this long so the user can retry.
const SUBMIT_CONFIRM_TIMEOUT_MS = 30000

interface EscalationPanelProps {
  meta: ChatMessageMeta
  onReply: (text: string) => void
  responded: boolean
}

function firstLine(text: string): string {
  return text.split('\n').map((line) => line.trim()).find(Boolean) ?? text
}

function checkpointStatusLabel(status: string): string {
  switch (status) {
    case 'timeout':
    case 'timed_out':
    case 'expired':
      return 'Expired'
    case 'stale':
    case 'invalid':
      return 'Inactive'
    case 'cancelled':
    case 'canceled':
      return 'Cancelled'
    case 'resolved':
      return 'Resolved'
    default:
      return 'Responded'
  }
}

export const EscalationPanel = React.memo(function EscalationPanel({
  meta, onReply, responded,
}: EscalationPanelProps) {
  const isResponded = responded
  const checkpointStatus = String(meta.checkpoint_status ?? '').trim().toLowerCase()
  const resolvedLabel = checkpointStatusLabel(checkpointStatus)
  const prompt = String(meta.prompt ?? meta.summary ?? '')
  const lines = useMemo(
    () => prompt.split('\n').map((line) => line.trim()).filter(Boolean),
    [prompt],
  )
  const title = firstLine(prompt).replace(/^\[[^\]]+\]\s*/, '') || 'Action Required'
  const details = lines.slice(1).join('\n').trim()
  const summary = String(meta.summary ?? '').trim()
  const options = (meta.options ?? []).filter((opt): opt is HumanEscalationOption => !!opt?.id)
  const activeSubagents = useMemo(
    () => (meta.active_subagents ?? []).filter((item) => !!item && typeof item === 'object'),
    [meta.active_subagents],
  )
  const permissionRequests = useMemo(
    () => (meta.permission_requests ?? []).filter((item) => !!item && typeof item === 'object'),
    [meta.permission_requests],
  )
  const worktreePath = String(meta.worktree_path ?? '').trim()
  const hasRuntimeState = activeSubagents.length > 0 || permissionRequests.length > 0 || !!worktreePath

  const [submittedOptionId, setSubmittedOptionId] = useState('')
  const isSubmitting = !!submittedOptionId && !isResponded

  useEffect(() => {
    if (!isSubmitting) return
    const timer = window.setTimeout(() => setSubmittedOptionId(''), SUBMIT_CONFIRM_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }, [isSubmitting, submittedOptionId])

  const handleReply = useCallback((option: HumanEscalationOption) => {
    if (isResponded || isSubmitting) return
    setSubmittedOptionId(option.id)
    onReply(option.label || option.id)
  }, [isResponded, isSubmitting, onReply])

  return (
    <div className="ckpt-panel ckpt-escalation">
      <div className="ckpt-header">
        <div className="ckpt-icon ckpt-icon-escalation">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 1.5L14.5 13H1.5L8 1.5Z" />
            <path d="M8 5.5V9" />
            <circle cx="8" cy="11.5" r="0.75" fill="currentColor" stroke="none" />
          </svg>
        </div>
        <div className="ckpt-title">{title}</div>
        <span className="ckpt-badge ckpt-badge-scope">
          {String(meta.escalation_type ?? 'decision_needed').replace(/_/g, ' ')}
        </span>
        {isResponded && <span className="ckpt-badge ckpt-badge-responded">{resolvedLabel}</span>}
      </div>

      {summary && summary !== title && (
        <div className="ckpt-section">
          <div className="ckpt-section-title">Summary</div>
          <MarkdownBody content={summary} className="ckpt-markdown" />
        </div>
      )}

      {details && (
        <div className="ckpt-section">
          <div className="ckpt-section-title">Request</div>
          <MarkdownBody content={details} className="ckpt-markdown" />
        </div>
      )}

      {hasRuntimeState && (
        <details className="ckpt-runtime-details">
          <summary>Runtime State</summary>
          <div className="ckpt-runtime-body">
            {worktreePath && <div>Worktree: <code>{worktreePath}</code></div>}
            {activeSubagents.length > 0 && <div>Active subagents: {activeSubagents.length}</div>}
            {permissionRequests.length > 0 && <div>Pending permission records: {permissionRequests.length}</div>}
          </div>
        </details>
      )}

      {!isResponded && options.length > 0 && (
        <div className="ckpt-actions ckpt-escalation-actions">
          {options.map((option) => (
            <button
              key={option.id}
              className={`ckpt-btn ${option.id.includes('deny') ? 'ckpt-btn-deny' : 'ckpt-btn-approve'}${isSubmitting ? ' ckpt-btn-submitting' : ''}`}
              onClick={() => handleReply(option)}
              disabled={isSubmitting}
            >
              {isSubmitting && submittedOptionId === option.id ? 'Submitting…' : (option.label || option.id)}
            </button>
          ))}
        </div>
      )}

      {isSubmitting && (
        <div className="ckpt-escalation-hint">
          Decision sent — waiting for server confirmation…
        </div>
      )}

      {!isResponded && meta.default_action && (
        <div className="ckpt-escalation-hint">
          Default on timeout: <code>{meta.default_action}</code>
        </div>
      )}
    </div>
  )
})
