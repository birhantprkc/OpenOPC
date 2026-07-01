import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import type { AttachmentRefMeta, ChatMessage, CheckpointReplyMetadata } from '../types/chat'
import type { ProgressEntry, RoleWorkItemSummary, Session, WorkItemProgressEntry } from '../types/kanban'
import { progressEntryKey } from '../lib/progressEntryKey'
import { IconCopy, IconCheck, IconChat, IconSparkle, IconShield, IconActivity, IconChevron } from './SvgIcons'
import { AgentProgressBlock, AgentProgressEntryCard, INLINE_PROGRESS_ENTRY_TYPES } from './AgentProgressBlock'
import { MarkdownBody } from './MarkdownBody'
import { RecruitmentPanel } from './RecruitmentPanel'
import { StaffingSelectionPanel } from './StaffingSelectionPanel'
import { ReorgPanel } from './ReorgPanel'
import { EscalationPanel } from './EscalationPanel'
import { DeliveryFeedbackPanel } from './DeliveryFeedbackPanel'
import { TaskUserInputPanel } from './TaskUserInputPanel'
import { WorkItemProgressCard } from './WorkItemProgressCard'
import { analyzeCheckpointMessages, isCheckpointCardMetadata, toCheckpointReplyMetadata } from './checkpointUtils'

export { MarkdownBody } from './MarkdownBody'

function formatAttachmentSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes}B`
  if (sizeBytes < 1048576) return `${(sizeBytes / 1024).toFixed(0)}KB`
  return `${(sizeBytes / 1048576).toFixed(1)}MB`
}

function attachmentBadgeLabel(mimeType: string, filename: string): string {
  const extension = filename.includes('.') ? filename.split('.').pop()?.toUpperCase() ?? '' : ''
  if (mimeType.startsWith('image/')) return 'IMG'
  if (mimeType === 'application/pdf') return 'PDF'
  if (mimeType.includes('wordprocessingml')) return 'DOC'
  if (mimeType.includes('spreadsheetml') || extension === 'CSV') return 'XLS'
  if (mimeType.includes('presentationml')) return 'PPT'
  if (mimeType.includes('json')) return 'JSON'
  if (mimeType.includes('yaml') || extension === 'YAML' || extension === 'YML') return 'YAML'
  if (mimeType.startsWith('text/')) return 'TXT'
  if (['PY', 'TS', 'TSX', 'JS', 'JSX', 'GO', 'RS', 'RB', 'JAVA', 'C', 'CPP', 'HTML', 'CSS', 'SH'].includes(extension)) return extension
  return extension || 'FILE'
}

function attachmentToneClass(mimeType: string, filename: string): string {
  const label = attachmentBadgeLabel(mimeType, filename)
  if (label === 'IMG') return 'image'
  if (label === 'PDF') return 'pdf'
  if (label === 'DOC' || label === 'XLS' || label === 'PPT') return 'office'
  if (label === 'JSON' || label === 'YAML') return 'data'
  if (label === 'TXT') return 'text'
  if (['PY', 'TS', 'TSX', 'JS', 'JSX', 'GO', 'RS', 'RB', 'JAVA', 'C', 'CPP', 'HTML', 'CSS', 'SH'].includes(label)) return 'code'
  return 'generic'
}

function AttachmentBlock({ refs, onImageClick }: { refs: AttachmentRefMeta[]; onImageClick?: (url: string) => void }) {
  if (!refs || refs.length === 0) return null
  const images = refs.filter(r => r.mime_type?.startsWith('image/'))
  const videos = refs.filter(r => r.mime_type?.startsWith('video/'))
  const files = refs.filter(r => !r.mime_type?.startsWith('image/') && !r.mime_type?.startsWith('video/'))
  const gridClass = images.length === 1 ? '' : images.length <= 3 ? ' cols-2' : ' cols-2'
  const videoGridClass = videos.length === 1 ? '' : videos.length <= 3 ? ' cols-2' : ' cols-2'
  return (
    <div className="msg-attachments">
      {images.length > 0 && (
        <div className={`msg-attachment-grid${gridClass}`}>
          {images.map(r => {
            const url = `/api/attachments/${r.attachment_id}/${r.filename}`
            return (
              <img
                key={r.attachment_id}
                className="msg-attachment-image"
                src={url}
                alt={r.filename}
                loading="lazy"
                onClick={() => onImageClick?.(url)}
              />
            )
          })}
        </div>
      )}
      {videos.length > 0 && (
        <div className={`msg-attachment-grid${videoGridClass}`}>
          {videos.map(r => {
            const url = `/api/attachments/${r.attachment_id}/${r.filename}`
            return (
              <video
                key={r.attachment_id}
                className="msg-attachment-video"
                src={url}
                controls
                preload="metadata"
                playsInline
              />
            )
          })}
        </div>
      )}
      {files.length > 0 && (
        <div className="msg-attachment-files">
          {files.map(r => (
            <a key={r.attachment_id} className="msg-attachment-file-chip" href={`/api/attachments/${r.attachment_id}/${r.filename}`} download title={r.filename}>
              📎 {r.filename}
              <span className="msg-attachment-file-size">{r.size_bytes < 1024 ? `${r.size_bytes}B` : r.size_bytes < 1048576 ? `${(r.size_bytes / 1024).toFixed(0)}KB` : `${(r.size_bytes / 1048576).toFixed(1)}MB`}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

type ViewKind = 'session' | 'activity' | 'secretary'
type DetailMode = 'summary' | 'full'

interface MessageListProps {
  messages: ChatMessage[]
  channelName: string
  viewKind?: ViewKind
  detailMode?: DetailMode
  agentStatus?: string
  currentTool?: string
  toolElapsedMs?: number
  lastToolSummary?: string
  progressLog?: ProgressEntry[]
  draftAssistantText?: string
  draftUpdatedAt?: number
  draftIteration?: number
  draftTurnId?: string
  isCompanyRuntime?: boolean
  workItemLog?: WorkItemProgressEntry[]
  childSessions?: Session[]
  /**
   * Per-role DelegationWorkItem rollup that drives the Execution Progress
   * panel. When present (company-mode primary sessions) it supersedes the
   * legacy session-derived rendering inside ``WorkItemProgressCard``.
   */
  roleWorkItems?: Record<string, RoleWorkItemSummary>
  /** Display-only executor-role rollup for the Execution Progress card. */
  executorRoleWorkItems?: Record<string, RoleWorkItemSummary>
  onSend?: (text: string, taskId?: string, metadata?: CheckpointReplyMetadata) => void
  onWorkItemClick?: (executionTurnId: string) => void
  onWorkItemOpenSession?: (executionTurnId: string) => void
  onMarkRead?: () => void
  hasOlderHistory?: boolean
  totalMessageCount?: number
  onLoadOlderHistory?: (oldestMessage?: ChatMessage) => Promise<void> | void
  loadingOlderHistory?: boolean
  autoScroll?: boolean
  initialScrollToBottom?: boolean
  showWorkItemRuntimeCard?: boolean
  showRuntimeProgress?: boolean
  renderUserMarkdown?: boolean
}

type TimelineItem =
  | { kind: 'message'; id: string; timestamp: number; msg: ChatMessage; sortOrder: number }
  | { kind: 'progress'; id: string; timestamp: number; entry: ProgressEntry; sortOrder: number }
  | { kind: 'draft'; id: string; timestamp: number; text: string; iteration?: number; sortOrder: number }
  | { kind: 'ops-bundle'; id: string; timestamp: number; events: SystemOpsBundleEvent[]; sortOrder: number }

interface ProjectUpdatePayload {
  kind: 'report' | 'review' | 'update'
  title?: string
  summary: string
  verdict?: string
  deliverables: Array<{ name: string; path: string; status?: string }>
  risks: string[]
  nextActions: string[]
  acceptanceSummary?: string
}

interface SystemOpsBundleEvent {
  msg: ChatMessage
  classification: SystemOpsClassification
}

/* ── Agent color palette ─────────────────────────────────────────────── */
const AGENT_PALETTE = [
  '#F59E0B', '#10B981', '#3B82F6', '#8B5CF6',
  '#EC4899', '#06B6D4', '#F97316', '#6366F1',
]

// Module-level cache so color lookups are O(1) across re-renders
const agentColorCache = new Map<string, string>()

function agentColor(sender: string): string {
  const cached = agentColorCache.get(sender)
  if (cached) return cached
  let h = 0
  for (let i = 0; i < sender.length; i++) h = (h * 31 + sender.charCodeAt(i)) | 0
  const color = AGENT_PALETTE[Math.abs(h) % AGENT_PALETTE.length]
  agentColorCache.set(sender, color)
  return color
}

/* ── Work-item event parser ───────────────────────────────────────────── */
const WORK_ITEM_RE = /^\[Company:([^\]]+)\]\s*(.*)$/

interface WorkItemInfo {
  projectionId: string
  workItemName: string
  action: string
  icon: string
  statusClass: string
}

function parseWorkItemEvent(content: string): WorkItemInfo | null {
  const m = WORK_ITEM_RE.exec(content)
  if (!m) return null
  const projectionId = m[1]
  const action = m[2].trim()
  const actionLower = action.toLowerCase()
  const workItemName = projectionId.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

  let icon = '\u25CF'       // ● default
  let statusClass = 'active'
  if (actionLower.includes('gate passed') || actionLower.includes('approved') || actionLower.includes('completed')) {
    icon = '\u2713'; statusClass = 'passed'
  } else if (actionLower.includes('rejected') || actionLower.includes('reworking')) {
    icon = '\u21BB'; statusClass = 'rejected'
  } else if (actionLower.includes('failed') || actionLower.includes('timed out')) {
    icon = '\u2717'; statusClass = 'failed'
  } else if (actionLower.includes('awaiting')) {
    icon = '\u23F3'; statusClass = 'waiting'
  }
  return { projectionId, workItemName, action, icon, statusClass }
}

const WELCOME: Record<ViewKind, { icon: React.ReactNode; title: string; hint: string }> = {
  session: {
    icon: <IconChat />,
    title: 'New Conversation',
    hint: 'Send a message to start working with your OPC system',
  },
  activity: {
    icon: <IconActivity />,
    title: 'Activity Feed',
    hint: 'Agent activity across all sessions will appear here.',
  },
  secretary: {
    icon: <IconShield />,
    title: 'Secretary',
    hint: 'Manage policies, rules, and preferences for your agents.',
  },
}

/* ── Grouping: consecutive same-sender within 5 min ────────────────── */
const GROUP_WINDOW = 5 * 60_000
const INITIAL_VISIBLE_TIMELINE_ITEMS = 200
const VISIBLE_TIMELINE_STEP = 200
const FOLLOW_BOTTOM_THRESHOLD_PX = 96
const SCROLL_TOP_EPSILON_PX = 1
const PROGRAMMATIC_SCROLL_GRACE_MS = 700

function isNearScrollBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_BOTTOM_THRESHOLD_PX
}

export function shouldReleaseStickToBottomOnScroll({
  previousScrollTop,
  nextScrollTop,
  atBottom,
  userScrolling,
  programmaticScroll,
}: {
  previousScrollTop: number
  nextScrollTop: number
  atBottom: boolean
  userScrolling: boolean
  programmaticScroll: boolean
}): boolean {
  if (programmaticScroll || atBottom) return false
  if (userScrolling) return true
  return nextScrollTop < previousScrollTop - SCROLL_TOP_EPSILON_PX
}

function formatTime(ts: number) {
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
}

function formatFullTime(ts: number) {
  return new Date(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Fall through to the textarea path for remote HTTP / denied clipboard contexts.
    }
  }

  if (typeof document === 'undefined' || !document.body) return false
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.top = '0'
  textarea.style.left = '-9999px'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)

  try {
    textarea.focus()
    textarea.select()
    textarea.setSelectionRange(0, textarea.value.length)
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(textarea)
  }
}

function messageVisibleInDetailMode(message: ChatMessage, detailMode: DetailMode): boolean {
  if (detailMode === 'full') return true
  return String(message.metadata?.detail_visibility ?? 'summary').trim() !== 'full'
}

function isExecutionContextMessage(message: ChatMessage): boolean {
  return String(message.metadata?.transcript_kind ?? '').trim() === 'runtime_v2_user_turn'
}

function compactWhitespace(value: string): string {
  return value.replace(/\s+/g, ' ').trim()
}

function stripNarrativeTitlePrefix(content: string): string {
  const trimmed = String(content || '').trim()
  const markdownTitle = trimmed.match(/^\*\*(.{8,160}?)\*\*:\s+([\s\S]+)$/)
  if (markdownTitle) {
    const body = markdownTitle[2].trim()
    if (body.length >= 80) return body
  }
  const colonIndex = trimmed.indexOf(': ')
  if (colonIndex < 8 || colonIndex > 160) return trimmed

  const prefix = trimmed.slice(0, colonIndex).replace(/\*/g, '').trim()
  const body = trimmed.slice(colonIndex + 2).trim()
  if (body.length < 80) return trimmed
  if (!/[A-Za-z\u4e00-\u9fff]/.test(prefix)) return trimmed
  if (/^(https?|file)$/i.test(prefix)) return trimmed
  return body
}

function isResultSurfaceMessage(message: ChatMessage): boolean {
  const transcriptKind = String(message.metadata?.transcript_kind ?? message.metadata?.kind ?? '').trim()
  if ([
    'runtime_v2_assistant',
    'runtime_v2_company_assistant',
    'top_level_reply',
    'company_role_result',
    'company_role_result_retry',
    'child_task_result',
    'child_task_result_retry',
    'child_result',
  ].includes(transcriptKind)) {
    return true
  }
  if (String(message.metadata?.kind ?? '').trim() === 'worker_notification') {
    return true
  }
  return false
}

function parseJsonObjectText(content: string): Record<string, unknown> | null {
  const trimmed = String(content || '').trim()
  if (!trimmed) return null
  try {
    const parsed = JSON.parse(trimmed)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null
  } catch {
    return null
  }
}

function extractProjectUpdateJson(content: string): { obj: Record<string, unknown>; title?: string } | null {
  const trimmed = String(content || '').trim()
  if (!trimmed) return null

  if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
    const obj = parseJsonObjectText(trimmed)
    return obj ? { obj } : null
  }

  const firstBrace = trimmed.indexOf('{')
  const lastBrace = trimmed.lastIndexOf('}')
  if (firstBrace <= 0 || lastBrace <= firstBrace) return null

  const rawPrefix = trimmed.slice(0, firstBrace).trim()
  const prefix = rawPrefix.replace(/\*/g, '').replace(/:\s*$/, '').trim()
  if (!/\b(report|review)\b/i.test(prefix)) return null

  const obj = parseJsonObjectText(trimmed.slice(firstBrace, lastBrace + 1))
  if (!obj) return null
  return { obj, title: prefix || undefined }
}

function stringFromUnknown(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function stringsFromUnknownList(value: unknown, limit = 4): string[] {
  if (!Array.isArray(value)) return []
  const out: string[] = []
  for (const item of value) {
    const text = typeof item === 'string'
      ? item.trim()
      : item && typeof item === 'object' && 'summary' in item
        ? stringFromUnknown((item as Record<string, unknown>).summary)
        : ''
    if (text) out.push(text)
    if (out.length >= limit) break
  }
  return out
}

function deliverablesFromUnknown(value: unknown): ProjectUpdatePayload['deliverables'] {
  if (!Array.isArray(value)) return []
  const out: ProjectUpdatePayload['deliverables'] = []
  for (const item of value) {
    if (!item || typeof item !== 'object') continue
    const rec = item as Record<string, unknown>
    const path = stringFromUnknown(rec.path)
    const name = stringFromUnknown(rec.name) || path.split('/').filter(Boolean).pop() || 'Artifact'
    if (!path && !name) continue
    out.push({
      name,
      path,
      status: stringFromUnknown(rec.status) || undefined,
    })
  }
  return out
}

function acceptanceSummaryFromUnknown(value: unknown): string | undefined {
  if (!Array.isArray(value)) return undefined
  const total = value.filter(item => item && typeof item === 'object').length
  if (!total) return undefined
  const met = value.filter((item) => {
    const rec = item as Record<string, unknown>
    return rec.met === true
  }).length
  return `${met}/${total} acceptance checks met`
}

export function parseProjectUpdatePayload(content: string): ProjectUpdatePayload | null {
  const extracted = extractProjectUpdateJson(content)
  if (!extracted) return null
  const { obj, title } = extracted

  const summary = stringFromUnknown(obj.summary)
  const verdict = stringFromUnknown(obj.review_verdict)
  const deliverables = deliverablesFromUnknown(obj.deliverables)
  const risks = stringsFromUnknownList(obj.risks)
  const nextActions = stringsFromUnknownList(obj.next_actions)
  const acceptanceSummary = acceptanceSummaryFromUnknown(obj.acceptance_status)

  if (!summary && !verdict && deliverables.length === 0 && !acceptanceSummary) return null

  const kind: ProjectUpdatePayload['kind'] = verdict
    ? 'review'
    : (deliverables.length > 0 || acceptanceSummary ? 'report' : 'update')

  return {
    kind,
    title,
    summary,
    verdict: verdict || undefined,
    deliverables,
    risks,
    nextActions,
    acceptanceSummary,
  }
}

/* ── Memoized message-row sub-components ─────────────────────────────── *
 * Extracting each row type into React.memo prevents re-rendering the
 * entire visible list when only a single new message is appended.       */

interface ProgressRowProps {
  entry: ProgressEntry
  showDate: boolean
  dateStr: string
  compact?: boolean
}
const ProgressRow = React.memo(function ProgressRow({ entry, showDate, dateStr, compact = false }: ProgressRowProps) {
  return (
    <div>
      {showDate && <div className="msg-date-sep"><span>{dateStr}</span></div>}
      <div className={`msg-row agent msg-row-inline-progress${compact ? ' compact' : ''}`}>
        <div className="msg-avatar agent-avatar"><IconSparkle /></div>
        <div className="msg-body msg-inline-progress-body">
          <div className="msg-inline-progress-shell">
            <AgentProgressEntryCard entry={entry} />
          </div>
        </div>
      </div>
    </div>
  )
})

interface WorkItemRowProps { msg: ChatMessage; showDate: boolean; dateStr: string }
const WorkItemRow = React.memo(function WorkItemRow({ msg, showDate, dateStr }: WorkItemRowProps) {
  const workItem = parseWorkItemEvent(msg.content)
  if (!workItem) return null
  return (
    <div>
      {showDate && <div className="msg-date-sep"><span>{dateStr}</span></div>}
      <div className={`work-item-divider work-item-divider-${workItem.statusClass}`}>
        <div className="work-item-divider-line" />
        <span className="work-item-divider-label">
          <span className="work-item-divider-icon">{workItem.icon}</span>
          {workItem.workItemName}
          <span className="work-item-divider-action">{workItem.action}</span>
        </span>
        <div className="work-item-divider-line" />
      </div>
    </div>
  )
})

interface SystemRowProps { msg: ChatMessage; showDate: boolean; dateStr: string }
const SystemRow = React.memo(function SystemRow({ msg, showDate, dateStr }: SystemRowProps) {
  return (
    <div>
      {showDate && <div className="msg-date-sep"><span>{dateStr}</span></div>}
      <div className="msg-system"><span>{msg.content}</span></div>
    </div>
  )
})

interface ContextRowProps { msg: ChatMessage; showDate: boolean; dateStr: string }
const ContextRow = React.memo(function ContextRow({ msg, showDate, dateStr }: ContextRowProps) {
  return (
    <div>
      {showDate && <div className="msg-date-sep"><span>{dateStr}</span></div>}
      <div className="msg-row agent msg-row-context">
        <div className="msg-avatar agent-avatar msg-avatar-context"><IconShield /></div>
        <div className="msg-body">
          <div className="msg-content-agent-card msg-context-card">
            <div className="msg-context-label">Execution Context</div>
            <MarkdownBody content={msg.content} />
          </div>
        </div>
      </div>
    </div>
  )
})

interface DraftRowProps {
  text: string
  timestamp: number
  iteration?: number
  showDate: boolean
  dateStr: string
}
const DraftRow = React.memo(function DraftRow({ text, timestamp, iteration, showDate, dateStr }: DraftRowProps) {
  return (
    <div>
      {showDate && <div className="msg-date-sep"><span>{dateStr}</span></div>}
      <div className="msg-row agent msg-row-draft">
        <div className="msg-avatar agent-avatar msg-avatar-draft"><IconSparkle /></div>
        <div className="msg-body">
          <div className="msg-agent-header">
            <span className="msg-sender">Live Reply</span>
            <span className="msg-time" title={formatFullTime(timestamp)}>
              {formatTime(timestamp)}
            </span>
          </div>
          <div className="msg-content-agent-card msg-draft-card">
            {iteration ? <div className="msg-draft-label">Turn {iteration}</div> : null}
            <MarkdownBody content={text} collapseMode="never" />
          </div>
        </div>
      </div>
    </div>
  )
})

interface UserRowProps {
  msg: ChatMessage
  showDate: boolean
  dateStr: string
  isGrouped: boolean
  isCopied: boolean
  onCopy: (id: string, content: string) => void
  onImageClick: (url: string) => void
  renderUserMarkdown: boolean
}
const UserRow = React.memo(function UserRow({ msg, showDate, dateStr, isGrouped, isCopied, onCopy, onImageClick, renderUserMarkdown }: UserRowProps) {
  return (
    <div>
      {showDate && <div className="msg-date-sep"><span>{dateStr}</span></div>}
      <div className={`msg-row user${isGrouped ? ' grouped' : ''}`}>
        <div className="msg-body msg-body-user">
          <div className="msg-content-user">
            {renderUserMarkdown ? (
              <MarkdownBody content={msg.content} className="msg-content-user-markdown" />
            ) : (
              msg.content
            )}
          </div>
          <AttachmentBlock refs={(msg.metadata as any)?.attachment_refs} onImageClick={onImageClick} />
          <div className="msg-meta-user">
            <span className="msg-time" title={formatFullTime(msg.timestamp)}>{formatTime(msg.timestamp)}</span>
            <button className="msg-action-btn" onClick={() => onCopy(msg.id, msg.content)} title="Copy">
              {isCopied ? <IconCheck /> : <IconCopy />}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
})

/**
 * Recognise OPC's operational system messages — the ones that historically
 * dumped raw command strings ("[Delegating to codex] task=... | cmd=codex
 * exec -C /Users/...") straight into the transcript. We pull these out into
 * a one-line collapsed log row instead of a full agent-card so the chat
 * reads as conversation, not as terminal output.
 */
interface SystemOpsClassification {
  kind: 'delegation' | 'external_status' | 'external_resume' | 'external_error' | 'company_event' | 'other'
  label: string
  summary: string
  tone: 'info' | 'success' | 'warning' | 'danger'
}

function classifySystemOps(content: string): SystemOpsClassification | null {
  const trimmed = String(content || '').trim()
  if (!trimmed) return null
  // [Delegating to codex] task=... | model=... | cmd=...
  const delegate = trimmed.match(/^\[Delegating to ([^\]]+)\]/)
  if (delegate) {
    const agent = delegate[1].trim()
    const taskMatch = trimmed.match(/task=([^|]+?)(?:\||$)/)
    const task = taskMatch ? taskMatch[1].trim() : ''
    return {
      kind: 'delegation',
      label: `Delegating to ${agent}`,
      summary: task,
      tone: 'info',
    }
  }
  if (/^\[External resume\]/i.test(trimmed)) {
    const sessionMatch = trimmed.match(/codex:[a-z0-9-]+:([a-f0-9-]+)/i)
    return {
      kind: 'external_resume',
      label: 'External resume',
      summary: sessionMatch ? `Restored session ${sessionMatch[1].slice(0, 8)}…` : 'Resumed prior session',
      tone: 'info',
    }
  }
  const inbox = trimmed.match(/^\[External inbox\]\s*(.+)$/i)
  if (inbox) {
    return {
      kind: 'external_status',
      label: 'External inbox',
      summary: compactWhitespace(inbox[1]),
      tone: 'info',
    }
  }
  const status = trimmed.match(/^\[External status\]\s*(.+)$/i)
  if (status) {
    const head = status[1].split(/\(|;/)[0].trim()
    return {
      kind: 'external_status',
      label: 'External status',
      summary: head || status[1].trim(),
      tone: 'info',
    }
  }
  if (/^\[External error\]/i.test(trimmed) || /^\[External failure\]/i.test(trimmed)) {
    return {
      kind: 'external_error',
      label: 'External error',
      summary: trimmed.replace(/^\[[^\]]+\]\s*/, '').split('\n')[0].slice(0, 140),
      tone: 'danger',
    }
  }
  // [Company:cto::execute::5dbd78ae] completed / starting / parked …
  const company = trimmed.match(/^\[Company:([^\]]+)\]\s*(.+)$/)
  if (company) {
    const scope = company[1].split('::')
    const role = scope[0] || 'company'
    const verb = company[2].trim()
    const lc = verb.toLowerCase()
    const tone: SystemOpsClassification['tone'] =
      lc.includes('completed') || lc.includes('done') ? 'success' :
      lc.includes('blocked') || lc.includes('failed') ? 'warning' :
      'info'
    return {
      kind: 'company_event',
      label: `${role.toUpperCase()} · ${scope[1] ?? ''}`.replace(/ · $/, ''),
      summary: verb,
      tone,
    }
  }
  const routineRoleStatus = trimmed.match(/^(Review needed|Status digest|Blocked|Completion):\s*(.+)$/i)
  if (routineRoleStatus) {
    const label = routineRoleStatus[1].replace(/\b\w/g, c => c.toUpperCase())
    const summary = compactWhitespace(routineRoleStatus[2])
    return {
      kind: 'company_event',
      label,
      summary,
      tone: /^Blocked$/i.test(routineRoleStatus[1]) ? 'warning' : 'info',
    }
  }
  const noDelegation = trimmed.match(/^NO_DELEGATION_JUSTIFICATION:\s*(.+)$/i)
  if (noDelegation) {
    return {
      kind: 'company_event',
      label: 'Delegation check',
      summary: compactWhitespace(noDelegation[1]).slice(0, 180),
      tone: 'info',
    }
  }
  return null
}

function systemOpsBundleEventForMessage(
  message: ChatMessage,
  options: {
    isCompanyRuntime: boolean | undefined
    detailMode: DetailMode
  },
): SystemOpsBundleEvent | null {
  const { isCompanyRuntime, detailMode } = options
  if (!isCompanyRuntime || detailMode === 'full') return null
  if (isCheckpointCardMetadata(message.metadata)) return null

  const classification = classifySystemOps(message.content)
  if (!classification) return null

  const isOperationalSender = message.sender === 'system' || message.metadata?.type === 'system'
  const isCompanyEvent = classification.kind === 'company_event' || message.content.startsWith('[Company:')
  if (!isOperationalSender && !isCompanyEvent) return null

  return { msg: message, classification }
}

export function buildNarrativeMessageItems(
  messages: ChatMessage[],
  options: {
    isCompanyRuntime?: boolean
    detailMode?: DetailMode
  } = {},
): TimelineItem[] {
  const { isCompanyRuntime = false, detailMode = 'summary' } = options
  const items: TimelineItem[] = []
  let bundle: SystemOpsBundleEvent[] = []
  let bundleSortOrder = 0
  const seenProjectUpdates = new Set<string>()
  const seenNarrativeMessages = new Set<string>()

  const flushBundle = () => {
    if (bundle.length === 0) return
    const first = bundle[0].msg
    const last = bundle[bundle.length - 1].msg
    items.push({
      kind: 'ops-bundle',
      id: `ops:${first.id}:${bundle.length}:${last.id}`,
      timestamp: first.timestamp,
      events: bundle,
      sortOrder: bundleSortOrder,
    })
    bundle = []
  }

  messages.forEach((msg, idx) => {
    const sortOrder = idx * 2 + 1
    const ops = systemOpsBundleEventForMessage(msg, { isCompanyRuntime, detailMode })
    if (ops) {
      if (bundle.length === 0) bundleSortOrder = sortOrder
      bundle.push(ops)
      return
    }
    const projectUpdate = detailMode === 'summary' ? parseProjectUpdatePayload(msg.content) : null
    if (projectUpdate) {
      const dedupeKey = [
        msg.sender,
        Math.round(msg.timestamp / 1000),
        projectUpdate.kind,
        compactWhitespace(projectUpdate.summary || projectUpdate.acceptanceSummary || '').slice(0, 500),
        projectUpdate.verdict ?? '',
        projectUpdate.deliverables.map(item => `${item.name}:${item.path}`).join('|').slice(0, 800),
      ].join('\u0001')
      if (seenProjectUpdates.has(dedupeKey)) return
      seenProjectUpdates.add(dedupeKey)
    }
    if (detailMode === 'summary') {
      const canonicalContent = compactWhitespace(stripNarrativeTitlePrefix(msg.content)).slice(0, 1200)
      if (canonicalContent) {
        const dedupeKey = isResultSurfaceMessage(msg)
          ? ['result', canonicalContent].join('\u0001')
          : [
              msg.sender,
              Math.round(msg.timestamp / 1000),
              canonicalContent,
            ].join('\u0001')
        if (seenNarrativeMessages.has(dedupeKey)) return
        seenNarrativeMessages.add(dedupeKey)
      }
    }
    flushBundle()
    items.push({
      kind: 'message',
      id: msg.id,
      timestamp: msg.timestamp,
      msg,
      sortOrder,
    })
  })
  flushBundle()
  return items
}

interface SystemOpsRowProps {
  msg: ChatMessage
  showDate: boolean
  dateStr: string
  isGrouped: boolean
  classification: SystemOpsClassification
}

const SystemOpsRow = React.memo(function SystemOpsRow({ msg, showDate, dateStr, classification }: SystemOpsRowProps) {
  const [expanded, setExpanded] = useState(false)
  const hasDetails = msg.content.trim() !== `[${classification.label}] ${classification.summary}`.trim()
    && msg.content.trim().length > classification.summary.length + 4
  return (
    <div>
      {showDate && <div className="msg-date-sep"><span>{dateStr}</span></div>}
      <div
        className={`msg-ops-row msg-ops-tone-${classification.tone}${expanded ? ' expanded' : ''}`}
        onClick={hasDetails ? () => setExpanded(v => !v) : undefined}
        role={hasDetails ? 'button' : undefined}
        tabIndex={hasDetails ? 0 : -1}
      >
        <span className="msg-ops-dot" aria-hidden="true" />
        <span className="msg-ops-label">{classification.label}</span>
        {classification.summary && (
          <span className="msg-ops-summary" title={classification.summary}>{classification.summary}</span>
        )}
        <span className="msg-ops-time" title={formatFullTime(msg.timestamp)}>{formatTime(msg.timestamp)}</span>
        {hasDetails && (
          <span className={`msg-ops-chevron${expanded ? ' open' : ''}`} aria-hidden="true">
            <IconChevron down={expanded} />
          </span>
        )}
      </div>
      {hasDetails && expanded && (
        <div className="msg-ops-details">
          <pre className="msg-ops-details-pre">{msg.content}</pre>
        </div>
      )}
    </div>
  )
})

interface OpsBundleRowProps {
  events: SystemOpsBundleEvent[]
  showDate: boolean
  dateStr: string
}

const OpsBundleRow = React.memo(function OpsBundleRow({ events, showDate, dateStr }: OpsBundleRowProps) {
  const [expanded, setExpanded] = useState(false)
  if (events.length === 0) return null

  const first = events[0].msg
  const last = events[events.length - 1].msg
  const counts = events.reduce<Record<string, number>>((acc, event) => {
    const label = event.classification.kind === 'company_event'
      ? 'company'
      : event.classification.kind.startsWith('external')
        ? 'runtime'
        : 'delegation'
    acc[label] = (acc[label] ?? 0) + 1
    return acc
  }, {})
  const summary = Object.entries(counts)
    .map(([label, count]) => `${count} ${label}`)
    .join(' · ')
  const title = `${events.length} technical event${events.length === 1 ? '' : 's'} hidden`

  return (
    <div>
      {showDate && <div className="msg-date-sep"><span>{dateStr}</span></div>}
      <button
        type="button"
        className={`msg-ops-bundle${expanded ? ' expanded' : ''}`}
        onClick={() => setExpanded(v => !v)}
        title={`${formatFullTime(first.timestamp)} - ${formatFullTime(last.timestamp)}`}
      >
        <span className="msg-ops-bundle-dot" />
        <span className="msg-ops-bundle-title">{title}</span>
        <span className="msg-ops-bundle-summary">{summary}</span>
        <span className="msg-ops-bundle-time">{formatTime(first.timestamp)}</span>
        <span className="msg-ops-bundle-chevron"><IconChevron down={expanded} /></span>
      </button>
      {expanded && (
        <div className="msg-ops-bundle-details">
          {events.map(({ msg, classification }) => (
            <div key={msg.id} className={`msg-ops-bundle-event msg-ops-tone-${classification.tone}`}>
              <span className="msg-ops-dot" />
              <span className="msg-ops-label">{classification.label}</span>
              <span className="msg-ops-summary">{classification.summary}</span>
              <span className="msg-ops-time" title={formatFullTime(msg.timestamp)}>{formatTime(msg.timestamp)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
})

interface ProjectUpdateRowProps {
  msg: ChatMessage
  payload: ProjectUpdatePayload
  showDate: boolean
  dateStr: string
  isCopied: boolean
  onCopy: (id: string, content: string) => void
}

function projectUpdateLabel(payload: ProjectUpdatePayload): string {
  if (payload.kind === 'review') {
    const verdict = payload.verdict ? payload.verdict.replace(/_/g, ' ') : 'review'
    return `Review · ${verdict}`
  }
  if (payload.kind === 'report') return 'Report'
  return 'Update'
}

const ProjectUpdateRow = React.memo(function ProjectUpdateRow({ msg, payload, showDate, dateStr, isCopied, onCopy }: ProjectUpdateRowProps) {
  const color = agentColor(msg.sender === 'system' ? (msg.senderName || 'OPC') : msg.sender)
  const displayName = msg.senderDeleted ? '[Deleted]' : msg.senderName
  const label = projectUpdateLabel(payload)
  const summary = payload.summary || payload.acceptanceSummary || label

  return (
    <div>
      {showDate && <div className="msg-date-sep"><span>{dateStr}</span></div>}
      <div className="msg-row agent msg-row-project-update">
        <div className="msg-avatar agent-avatar" style={{ background: color }}>
          {displayName.charAt(0).toUpperCase()}
        </div>
        <div className="msg-body">
          <div className="msg-agent-header">
            <span className="msg-sender" style={{ color }}>{displayName}</span>
            <span className="msg-time" title={formatFullTime(msg.timestamp)}>{formatTime(msg.timestamp)}</span>
          </div>
          <div className={`msg-project-update-card msg-project-update-${payload.kind}`}>
            <div className="msg-project-update-head">
              <span className="msg-project-update-label">{label}</span>
              {payload.acceptanceSummary && (
                <span className="msg-project-update-chip">{payload.acceptanceSummary}</span>
              )}
            </div>
            {payload.title && <div className="msg-project-update-title">{payload.title}</div>}
            <MarkdownBody content={summary} className="msg-project-update-summary" />
            {payload.deliverables.length > 0 && (
              <div className="msg-project-update-section">
                <div className="msg-project-update-section-label">Outputs</div>
                <div className="msg-project-update-artifacts">
                  {payload.deliverables.slice(0, 6).map((artifact, index) => (
                    <div key={`${artifact.path || artifact.name}-${index}`} className="msg-project-update-artifact">
                      <span className="msg-project-update-artifact-name">{artifact.name}</span>
                      {artifact.status && <span className="msg-project-update-artifact-status">{artifact.status}</span>}
                      {artifact.path && <code className="msg-project-update-artifact-path">{artifact.path}</code>}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {(payload.risks.length > 0 || payload.nextActions.length > 0) && (
              <div className="msg-project-update-grid">
                {payload.risks.length > 0 && (
                  <div className="msg-project-update-section">
                    <div className="msg-project-update-section-label">Caveats</div>
                    <ul className="msg-project-update-list">
                      {payload.risks.map((risk, index) => <li key={`risk-${index}`}>{risk}</li>)}
                    </ul>
                  </div>
                )}
                {payload.nextActions.length > 0 && (
                  <div className="msg-project-update-section">
                    <div className="msg-project-update-section-label">Next</div>
                    <ul className="msg-project-update-list">
                      {payload.nextActions.map((action, index) => <li key={`next-${index}`}>{action}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            )}
            <div className="msg-card-actions">
              <button className="msg-action-btn" onClick={() => onCopy(msg.id, msg.content)} title="Copy raw update">
                {isCopied ? <><IconCheck /> <span>Copied</span></> : <><IconCopy /> <span>Copy raw</span></>}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
})

interface AgentRowProps {
  msg: ChatMessage
  showDate: boolean
  dateStr: string
  isGrouped: boolean
  isCopied: boolean
  isCheckpointResponded: boolean
  suppressCheckpointPanel: boolean
  onCopy: (id: string, content: string) => void
  onSend?: (text: string, taskId?: string, metadata?: CheckpointReplyMetadata) => void
}
const AgentRow = React.memo(function AgentRow({ msg, showDate, dateStr, isGrouped, isCopied, isCheckpointResponded, suppressCheckpointPanel, onCopy, onSend }: AgentRowProps) {
  const isDeleted = !!msg.senderDeleted
  const displayName = isDeleted ? '[Deleted]' : msg.senderName
  const color = agentColor(msg.sender === 'system' ? (msg.senderName || 'OPC') : msg.sender)
  const replyTaskId = msg.channelId.startsWith('session:')
    ? msg.channelId.slice('session:'.length)
    : (msg.metadata?.taskId ?? msg.metadata?.task_id)
  const checkpointType = String(msg.metadata?.checkpoint_type ?? '').trim()
  const checkpointReplyMeta = toCheckpointReplyMetadata(msg.metadata)
  const hasCheckpointPanel = isCheckpointCardMetadata(msg.metadata) && !suppressCheckpointPanel

  return (
    <div>
      {showDate && <div className="msg-date-sep"><span>{dateStr}</span></div>}
      <div className={`msg-row agent${isGrouped ? ' grouped' : ''}${isDeleted ? ' deleted-sender' : ''}`}>
        {!isGrouped ? (
          <div className="msg-avatar agent-avatar" style={{ background: color }}>
            {displayName.charAt(0).toUpperCase()}
          </div>
        ) : (
          <div className="msg-avatar-spacer" />
        )}
        <div className="msg-body">
          {!isGrouped && (
            <div className="msg-agent-header">
              <span className={`msg-sender${isDeleted ? ' deleted' : ''}`} style={{ color }}>
                {displayName}
              </span>
              <span className="msg-time" title={formatFullTime(msg.timestamp)}>{formatTime(msg.timestamp)}</span>
            </div>
          )}
          {msg.replyToId && <div className="msg-reply-indicator">Replying to previous message</div>}
          {hasCheckpointPanel ? (
            <>
              {checkpointType === 'company_recruitment_confirmation' && (
                <RecruitmentPanel
                  meta={msg.metadata!}
                  onReply={(text, extraMetadata) => onSend?.(text, replyTaskId, extraMetadata)}
                  responded={isCheckpointResponded}
                />
              )}
              {checkpointType === 'company_staffing_selection' && (
                <StaffingSelectionPanel
                  meta={msg.metadata!}
                  onReply={(text, extraMetadata) => onSend?.(text, replyTaskId, extraMetadata)}
                  responded={isCheckpointResponded}
                />
              )}
              {checkpointType === 'company_work_item_gate' && (
                <EscalationPanel
                  meta={msg.metadata!}
                  onReply={(text) => onSend?.(text, replyTaskId, checkpointReplyMeta)}
                  responded={isCheckpointResponded}
                />
              )}
              {checkpointType === 'company_delivery_feedback' && (
                <DeliveryFeedbackPanel
                  meta={msg.metadata!}
                  onReply={(text, extraMetadata) => onSend?.(text, replyTaskId, extraMetadata ?? checkpointReplyMeta)}
                  responded={isCheckpointResponded}
                />
              )}
              {checkpointType === 'company_reorg_pending' && (
                <ReorgPanel
                  meta={msg.metadata!}
                  onReply={(text) => onSend?.(text, replyTaskId, checkpointReplyMeta)}
                  responded={isCheckpointResponded}
                />
              )}
              {checkpointType === 'human_escalation' && (
                <EscalationPanel
                  meta={msg.metadata!}
                  onReply={(text) => onSend?.(text, replyTaskId, checkpointReplyMeta)}
                  responded={isCheckpointResponded}
                />
              )}
              {checkpointType === 'task_user_input' && (
                <TaskUserInputPanel
                  meta={msg.metadata!}
                  onReply={(text, extraMetadata) => onSend?.(text, replyTaskId, { ...checkpointReplyMeta, ...extraMetadata } as CheckpointReplyMetadata)}
                  responded={isCheckpointResponded}
                />
              )}
            </>
          ) : (
            <div className="msg-content-agent-card" style={{ borderLeftColor: color }}>
              <MarkdownBody content={msg.content} />
              <div className="msg-card-actions">
                <button className="msg-action-btn" onClick={() => onCopy(msg.id, msg.content)} title="Copy message">
                  {isCopied ? <><IconCheck /> <span>Copied</span></> : <><IconCopy /> <span>Copy</span></>}
                </button>
                {isGrouped && (
                  <span className="msg-time msg-time-inline" title={formatFullTime(msg.timestamp)}>{formatTime(msg.timestamp)}</span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
})

export const MessageList = React.memo(function MessageList({
  messages,
  channelName,
  viewKind = 'session',
  detailMode = 'summary',
  agentStatus,
  currentTool,
  toolElapsedMs,
  lastToolSummary,
  progressLog,
  draftAssistantText,
  draftUpdatedAt,
  draftIteration,
  draftTurnId,
  isCompanyRuntime,
  workItemLog,
  childSessions,
  roleWorkItems,
  executorRoleWorkItems,
  onSend,
  onWorkItemClick,
  onWorkItemOpenSession,
  onMarkRead,
  hasOlderHistory = false,
  totalMessageCount,
  onLoadOlderHistory,
  loadingOlderHistory = false,
  autoScroll = true,
  initialScrollToBottom = true,
  showWorkItemRuntimeCard = true,
  showRuntimeProgress = true,
  renderUserMarkdown = false,
}: MessageListProps) {
  const listRef = useRef<HTMLDivElement>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null)
  const [visibleTimelineCount, setVisibleTimelineCount] = useState(INITIAL_VISIBLE_TIMELINE_ITEMS)
  const prevStatusRef = useRef(agentStatus)
  const stickRef = useRef(autoScroll)
  const initialScrollPendingRef = useRef(initialScrollToBottom)
  // Track whether the user is actively scrolling (mouse/touch/wheel interaction).
  // While true we suppress ALL programmatic scroll-to-bottom so the user can
  // freely browse history without being yanked back down.
  const userScrollingRef = useRef(false)
  const userScrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const programmaticScrollRef = useRef(false)
  const programmaticScrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastScrollTopRef = useRef(0)
  // Track the last message count we auto-scrolled for, so we only scroll when
  // genuinely new messages arrive — not on virtualizer re-measurement or other
  // derived-value changes.
  const lastAutoScrollCountRef = useRef(0)
  const contentSpacerRef = useRef<HTMLDivElement>(null)
  const pendingScrollFrameRef = useRef<number | null>(null)
  const pendingSecondScrollFrameRef = useRef<number | null>(null)
  const pendingScrollMarkReadRef = useRef(false)
  const pendingScrollForceRef = useRef(false)

  /* ── Detect user-initiated scroll vs programmatic scroll ──── *
   * We listen for wheel / pointerdown on the scroll container.  *
   * When detected we set userScrollingRef = true and keep it    *
   * true for 1.5 s after the last interaction.  This prevents   *
   * the virtualizer's internal re-measurement (which fires the  *
   * onScroll handler) from falsely re-enabling stick-to-bottom. */
  useEffect(() => {
    const el = listRef.current
    if (!el) return
    lastScrollTopRef.current = el.scrollTop
    const markUserScrolling = () => {
      userScrollingRef.current = true
      if (userScrollTimerRef.current) clearTimeout(userScrollTimerRef.current)
      userScrollTimerRef.current = setTimeout(() => {
        userScrollingRef.current = false
      }, 1500)
    }
    const handleWheel = (event: WheelEvent) => {
      markUserScrolling()
      if (event.deltaY < 0 && el.scrollTop > 0) {
        stickRef.current = false
      } else if (isNearScrollBottom(el)) {
        stickRef.current = autoScroll
      }
    }
    el.addEventListener('wheel', handleWheel, { passive: true })
    el.addEventListener('pointerdown', markUserScrolling)
    el.addEventListener('touchstart', markUserScrolling, { passive: true })
    return () => {
      el.removeEventListener('wheel', handleWheel)
      el.removeEventListener('pointerdown', markUserScrolling)
      el.removeEventListener('touchstart', markUserScrolling)
      if (userScrollTimerRef.current) clearTimeout(userScrollTimerRef.current)
    }
  }, [autoScroll])

  useEffect(() => {
    return () => {
      if (programmaticScrollTimerRef.current) clearTimeout(programmaticScrollTimerRef.current)
    }
  }, [])

  /* ── Smart auto-scroll + mark-read ────────────────────────── *
   * Only update stickRef when the user is NOT actively scrolling *
   * via wheel/pointer.  This prevents virtualizer re-measurement *
   * from falsely flipping stickRef back to true.                 */
  const handleScroll = useCallback(() => {
    const el = listRef.current
    if (!el) return
    const previousScrollTop = lastScrollTopRef.current
    const nextScrollTop = el.scrollTop
    lastScrollTopRef.current = nextScrollTop
    const atBottom = isNearScrollBottom(el)
    // Only update stick state from genuine user scroll, not from
    // programmatic scrolls or virtualizer re-measurement.
    if (programmaticScrollRef.current) {
      if (atBottom) onMarkRead?.()
      return
    }
    if (shouldReleaseStickToBottomOnScroll({
      previousScrollTop,
      nextScrollTop,
      atBottom,
      userScrolling: userScrollingRef.current,
      programmaticScroll: false,
    })) {
      stickRef.current = false
      return
    }
    if (userScrollingRef.current) {
      stickRef.current = atBottom
      if (atBottom) onMarkRead?.()
      return
    }
    if (atBottom) {
      stickRef.current = autoScroll
      onMarkRead?.()
    }
  }, [autoScroll, onMarkRead])

  const workItemLogLen = workItemLog?.length ?? 0
  const workItemCount = workItemLogLen + (childSessions?.length ?? 0)
  const filteredMessages = useMemo(() => {
    const visibleMessages = messages.filter(message => messageVisibleInDetailMode(message, detailMode))
    if (!isCompanyRuntime) return visibleMessages
    return visibleMessages.filter((message) => {
      const isCompanySystemMessage = message.metadata?.type === 'system' && message.content.startsWith('[Company:')
      if (isCompanySystemMessage) return false
      if (!showRuntimeProgress && message.content.startsWith('[Company:')) {
        return false
      }
      return true
    })
  }, [detailMode, isCompanyRuntime, messages, showRuntimeProgress])
  const floatPendingCheckpoints = viewKind === 'session' && !!onSend
  const checkpointAnalysis = useMemo(
    () => analyzeCheckpointMessages(filteredMessages),
    [filteredMessages],
  )
  const pendingCheckpointMessages = useMemo(
    () => floatPendingCheckpoints
      ? filteredMessages.filter(message => checkpointAnalysis.pendingMessageIds.has(message.id))
      : [],
    [checkpointAnalysis.pendingMessageIds, filteredMessages, floatPendingCheckpoints],
  )
  const timelineMessages = useMemo(
    () => floatPendingCheckpoints
      ? filteredMessages.filter(message => !checkpointAnalysis.pendingMessageIds.has(message.id))
      : filteredMessages,
    [checkpointAnalysis.pendingMessageIds, filteredMessages, floatPendingCheckpoints],
  )
  const thinkingProgressTurnIds = useMemo(() => {
    const ids = new Set<string>()
    for (const entry of progressLog ?? []) {
      if (entry.type !== 'thinking') continue
      const turnId = String(entry.turnId ?? '').trim()
      if (turnId) ids.add(turnId)
    }
    return ids
  }, [progressLog])
  const synthesizedThinkingEntries = useMemo(() => {
    const entries: ProgressEntry[] = []
    for (const message of timelineMessages) {
      const thinking = String(message.metadata?.runtime_thinking ?? '').trim()
      if (!thinking) continue
      const turnId = String(message.metadata?.canonical_turn_id ?? message.metadata?.turn_id ?? '').trim()
      if (turnId && thinkingProgressTurnIds.has(turnId)) continue
      entries.push({
        type: 'thinking' as const,
        summary: 'Thinking',
        detail: thinking,
        timestamp: Math.max(0, message.timestamp - 1),
        turnId: turnId || undefined,
        itemId: turnId ? `${turnId}:thinking` : `thinking:${message.id}`,
        streamId: turnId ? `${turnId}:thinking` : `thinking:${message.id}`,
        executionMode: String(message.metadata?.execution_mode ?? '').trim() || undefined,
      })
    }
    return entries
  }, [thinkingProgressTurnIds, timelineMessages])
  const inlineProgressEntries = useMemo(
    () => showRuntimeProgress
      ? [
        ...(progressLog ?? []).filter(entry => INLINE_PROGRESS_ENTRY_TYPES.has(entry.type)),
        ...synthesizedThinkingEntries,
      ].sort((a, b) => a.timestamp - b.timestamp)
      : [],
    [progressLog, showRuntimeProgress, synthesizedThinkingEntries],
  )
  const secondaryProgressEntries = useMemo(
    () => showRuntimeProgress
      ? (progressLog ?? []).filter(entry => !INLINE_PROGRESS_ENTRY_TYPES.has(entry.type))
      : [],
    [progressLog, showRuntimeProgress],
  )
  const timelineProgressEntries = useMemo(
    () => !showRuntimeProgress
      ? []
      : detailMode === 'full'
      ? (progressLog ?? [])
      : inlineProgressEntries,
    [detailMode, inlineProgressEntries, progressLog, showRuntimeProgress],
  )
  const bottomProgressEntries = useMemo(
    () => secondaryProgressEntries,
    [secondaryProgressEntries],
  )
  const draftTimelineItem = useMemo(() => {
    const text = String(draftAssistantText ?? '').trim()
    if (!text) return null
    return {
      kind: 'draft' as const,
      id: `draft-${draftTurnId ?? draftIteration ?? 'active'}`,
      timestamp: draftUpdatedAt ?? Date.now(),
      text,
      iteration: draftIteration,
      sortOrder: Number.MAX_SAFE_INTEGER,
    }
  }, [draftAssistantText, draftIteration, draftTurnId, draftUpdatedAt])
  const isAgentWorking = !!agentStatus && agentStatus !== 'idle'
  const hasProgressLog = bottomProgressEntries.length > 0
  const showProgressBlock = showRuntimeProgress && detailMode !== 'full' && (isAgentWorking || hasProgressLog)
  const timelineItems = useMemo<TimelineItem[]>(() => {
    const merged: TimelineItem[] = [
      ...buildNarrativeMessageItems(timelineMessages, { isCompanyRuntime, detailMode }),
      ...timelineProgressEntries.map((entry, idx) => ({
        kind: 'progress' as const,
        id: `progress-${progressEntryKey(entry, idx)}`,
        timestamp: entry.timestamp,
        entry,
        sortOrder: idx * 2,
      })),
    ]
    if (draftTimelineItem) merged.push(draftTimelineItem)
    return merged.sort((a, b) => a.timestamp - b.timestamp || a.sortOrder - b.sortOrder)
  }, [detailMode, draftTimelineItem, isCompanyRuntime, timelineMessages, timelineProgressEntries])
  const hiddenTimelineCount = Math.max(0, timelineItems.length - visibleTimelineCount)
  const visibleTimelineItems = useMemo(
    () => (hiddenTimelineCount > 0 ? timelineItems.slice(-visibleTimelineCount) : timelineItems),
    [hiddenTimelineCount, timelineItems, visibleTimelineCount],
  )

  useEffect(() => {
    setVisibleTimelineCount(INITIAL_VISIBLE_TIMELINE_ITEMS)
  }, [channelName, viewKind])

  useEffect(() => {
    stickRef.current = autoScroll
    initialScrollPendingRef.current = initialScrollToBottom
  }, [channelName, viewKind, autoScroll, initialScrollToBottom])

  /* ── Pre-process: filter, dates + grouping ──────────────────── */
  const processed = useMemo(() => {
    let lastDate = ''
    return visibleTimelineItems.map((item, idx) => {
      const dateStr = new Date(item.timestamp).toLocaleDateString()
      const showDate = dateStr !== lastDate
      if (showDate) lastDate = dateStr

      const prev = idx > 0 ? visibleTimelineItems[idx - 1] : null
      const isGrouped = item.kind === 'message'
        && prev?.kind === 'message'
        && !showDate
        && prev.msg.sender === item.msg.sender
        && prev.msg.senderName === item.msg.senderName
        && item.msg.metadata?.type !== 'system'
        && prev.msg.metadata?.type !== 'system'
        && !(item.msg.metadata as any)?.is_work_item_event
        && !(prev.msg.metadata as any)?.is_work_item_event
        && item.msg.timestamp - prev.msg.timestamp < GROUP_WINDOW

      return { item, showDate, dateStr, isGrouped }
    })
  }, [visibleTimelineItems])

  const copyMsg = useCallback((id: string, content: string) => {
    void copyTextToClipboard(content).then((copied) => {
      if (!copied) return
      setCopiedId(id)
      setTimeout(() => setCopiedId(null), 1500)
    })
  }, [])

  const handleLoadOlder = useCallback(async () => {
    if (hiddenTimelineCount > 0) {
      setVisibleTimelineCount((prev) => Math.min(timelineItems.length, prev + VISIBLE_TIMELINE_STEP))
      return
    }
    if (!hasOlderHistory || loadingOlderHistory || !onLoadOlderHistory) return
    await onLoadOlderHistory(filteredMessages[0])
    setVisibleTimelineCount((prev) => prev + VISIBLE_TIMELINE_STEP)
  }, [
    filteredMessages,
    hasOlderHistory,
    hiddenTimelineCount,
    loadingOlderHistory,
    onLoadOlderHistory,
    timelineItems.length,
  ])

  const welcome = WELCOME[viewKind]

  /* ── Build the flat list of renderable items for the virtualizer ──── *
   * We append work-item card, progress block, and pending checkpoints as
   * extra "virtual items" at the end so they participate in the same
   * virtualised scroll container and don't force a separate DOM tree.   */
  const hasWorkItemRuntimeCard = !!(
    detailMode !== 'full'
    && showWorkItemRuntimeCard
    && isCompanyRuntime
    && workItemCount > 0
  )
  const hasPendingCheckpoints = pendingCheckpointMessages.length > 0

  type VirtualItem =
    | { kind: 'history-hint' }
    | { kind: 'timeline'; idx: number }
    | { kind: 'work-item-runtime-card' }
    | { kind: 'progress-block' }
    | { kind: 'pending-section' }
    | { kind: 'end-anchor' }

  const virtualItems = useMemo<VirtualItem[]>(() => {
    const items: VirtualItem[] = []
    if (hiddenTimelineCount > 0 || hasOlderHistory) items.push({ kind: 'history-hint' })
    for (let i = 0; i < processed.length; i++) items.push({ kind: 'timeline', idx: i })
    if (hasWorkItemRuntimeCard) items.push({ kind: 'work-item-runtime-card' })
    if (showProgressBlock) items.push({ kind: 'progress-block' })
    if (hasPendingCheckpoints) items.push({ kind: 'pending-section' })
    items.push({ kind: 'end-anchor' })
    return items
  }, [processed.length, hiddenTimelineCount, hasOlderHistory, hasWorkItemRuntimeCard, showProgressBlock, hasPendingCheckpoints])

  /* ── Virtualizer: only mount DOM nodes for visible rows ──────────── */
  const virtualizer = useVirtualizer({
    count: virtualItems.length,
    getScrollElement: () => listRef.current,
    estimateSize: (index) => {
      const item = virtualItems[index]
      if (item.kind === 'end-anchor') return 1
      if (item.kind === 'history-hint') return 48
      if (item.kind === 'work-item-runtime-card') return 120
      if (item.kind === 'progress-block') return 80
      if (item.kind === 'pending-section') return 200
      // Timeline items: estimate based on content length for better initial layout
      const p = processed[item.idx]
      if (p?.item.kind === 'message') {
        const len = p.item.msg.content.length
        if (len < 100) return 72
        if (len < 500) return 120
        if (len < 2000) return 200
        return 300 // Long messages (will be collapsed anyway)
      }
      if (p?.item.kind === 'draft') {
        const len = p.item.text.length
        return len < 400 ? 120 : 200
      }
      if (p?.item.kind === 'ops-bundle') {
        return p.item.events.length > 8 ? 92 : 48
      }
      return 80
    },
    overscan: 15, // Render 15 extra items above/below viewport
    getItemKey: (index) => {
      const item = virtualItems[index]
      if (item.kind === 'timeline') return processed[item.idx]?.item.id ?? `tl-${item.idx}`
      return item.kind
    },
  })

  /* ── Auto-scroll to bottom via virtualizer ───────────────────────── */
  const virtualizerRef = useRef(virtualizer)
  virtualizerRef.current = virtualizer
  const virtualItemsLenRef = useRef(virtualItems.length)
  virtualItemsLenRef.current = virtualItems.length

  const markProgrammaticScroll = useCallback(() => {
    programmaticScrollRef.current = true
    if (programmaticScrollTimerRef.current) clearTimeout(programmaticScrollTimerRef.current)
    programmaticScrollTimerRef.current = setTimeout(() => {
      programmaticScrollRef.current = false
    }, PROGRAMMATIC_SCROLL_GRACE_MS)
  }, [])

  const cancelScheduledScroll = useCallback(() => {
    if (pendingScrollFrameRef.current !== null) {
      window.cancelAnimationFrame(pendingScrollFrameRef.current)
      pendingScrollFrameRef.current = null
    }
    if (pendingSecondScrollFrameRef.current !== null) {
      window.cancelAnimationFrame(pendingSecondScrollFrameRef.current)
      pendingSecondScrollFrameRef.current = null
    }
    pendingScrollMarkReadRef.current = false
    pendingScrollForceRef.current = false
  }, [])

  const scrollToEnd = useCallback(() => {
    const len = virtualItemsLenRef.current
    markProgrammaticScroll()
    if (len > 0) {
      virtualizerRef.current.scrollToIndex(len - 1, { align: 'end' })
    }
    const el = listRef.current
    if (el) {
      el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight)
      lastScrollTopRef.current = el.scrollTop
    }
  }, [markProgrammaticScroll])

  const scheduleScrollToEnd = useCallback((markRead = false, force = false) => {
    pendingScrollMarkReadRef.current ||= markRead
    pendingScrollForceRef.current ||= force
    if (pendingScrollFrameRef.current !== null || pendingSecondScrollFrameRef.current !== null) return

    pendingScrollFrameRef.current = window.requestAnimationFrame(() => {
      pendingScrollFrameRef.current = null
      if (!pendingScrollForceRef.current && (!stickRef.current || userScrollingRef.current)) {
        pendingScrollMarkReadRef.current = false
        pendingScrollForceRef.current = false
        return
      }
      scrollToEnd()
      pendingSecondScrollFrameRef.current = window.requestAnimationFrame(() => {
        pendingSecondScrollFrameRef.current = null
        const shouldMarkRead = pendingScrollMarkReadRef.current
        const shouldForce = pendingScrollForceRef.current
        pendingScrollMarkReadRef.current = false
        pendingScrollForceRef.current = false
        if (!shouldForce && (!stickRef.current || userScrollingRef.current)) return
        scrollToEnd()
        if (shouldMarkRead) onMarkRead?.()
      })
    })
  }, [onMarkRead, scrollToEnd])

  useEffect(() => cancelScheduledScroll, [cancelScheduledScroll])

  useEffect(() => {
    if (!autoScroll || typeof ResizeObserver === 'undefined') return
    const el = contentSpacerRef.current
    if (!el) return
    const observer = new ResizeObserver(() => {
      if (initialScrollPendingRef.current || !stickRef.current) return
      scheduleScrollToEnd(false)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [autoScroll, scheduleScrollToEnd, virtualItems.length])

  // Stable message count — only changes when actual messages arrive, not on
  // virtualizer re-measurement or other derived-value changes.
  const messageCount = timelineMessages.length

  const tailFollowKey = useMemo(() => {
    const tail = timelineItems[timelineItems.length - 1]
    const tailKey = tail?.kind === 'message'
      ? `message:${tail.id}:${tail.msg.content.length}:${tail.timestamp}`
      : tail?.kind === 'draft'
      ? `draft:${tail.id}:${tail.text.length}:${tail.timestamp}`
      : tail?.kind === 'progress'
      ? `progress:${tail.id}:${tail.entry.summary.length}:${tail.entry.detail?.length ?? 0}:${tail.timestamp}`
      : tail?.kind === 'ops-bundle'
      ? `ops:${tail.id}:${tail.events.length}:${tail.timestamp}`
      : 'empty'
    const bottomTail = bottomProgressEntries[bottomProgressEntries.length - 1]
    const bottomProgressKey = bottomTail
      ? `${bottomTail.type}:${bottomTail.summary.length}:${bottomTail.detail?.length ?? 0}:${bottomTail.timestamp}`
      : 'none'
    return [
      virtualItems.length,
      messageCount,
      tailKey,
      bottomProgressEntries.length,
      bottomProgressKey,
      pendingCheckpointMessages.length,
      workItemCount,
      showProgressBlock ? `${agentStatus ?? ''}:${currentTool ?? ''}` : '',
    ].join('|')
  }, [
    agentStatus,
    bottomProgressEntries,
    currentTool,
    messageCount,
    pendingCheckpointMessages.length,
    showProgressBlock,
    timelineItems,
    virtualItems.length,
    workItemCount,
  ])

  useLayoutEffect(() => {
    const hasRenderableContent =
      timelineItems.length > 0
      || pendingCheckpointMessages.length > 0
      || workItemCount > 0
      || showProgressBlock

    if (!initialScrollPendingRef.current || !hasRenderableContent) return
    initialScrollPendingRef.current = false
    lastAutoScrollCountRef.current = messageCount
    scheduleScrollToEnd(autoScroll, true)
  // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally only on initial content
  }, [timelineItems.length > 0, showProgressBlock])

  // Auto-scroll when NEW messages arrive — only if user hasn't scrolled away.
  // We compare messageCount to lastAutoScrollCountRef to avoid firing on
  // virtualizer re-measurement or other derived-value changes.
  useEffect(() => {
    if (!autoScroll || initialScrollPendingRef.current) return
    if (!stickRef.current) return
    if (messageCount <= lastAutoScrollCountRef.current) return
    lastAutoScrollCountRef.current = messageCount
    scheduleScrollToEnd(true)
  }, [messageCount, autoScroll, scheduleScrollToEnd])

  // Auto-scroll when agent status transitions to active (starts working)
  useEffect(() => {
    const statusBecameActive = prevStatusRef.current === 'idle' && !!agentStatus && agentStatus !== 'idle'
    prevStatusRef.current = agentStatus
    if (!autoScroll || initialScrollPendingRef.current || !stickRef.current || !statusBecameActive) return
    scheduleScrollToEnd(true)
  }, [agentStatus, autoScroll, scheduleScrollToEnd])

  useEffect(() => {
    if (!autoScroll || initialScrollPendingRef.current || !stickRef.current) return
    if (!draftTimelineItem) return
    scheduleScrollToEnd(true)
  }, [autoScroll, draftTimelineItem, scheduleScrollToEnd])

  useLayoutEffect(() => {
    if (!autoScroll || initialScrollPendingRef.current || !stickRef.current) return
    scheduleScrollToEnd(false)
  }, [autoScroll, scheduleScrollToEnd, tailFollowKey])

  /* ── Render a single virtual row ─────────────────────────────────── */
  const renderVirtualRow = useCallback((vItem: VirtualItem) => {
    if (vItem.kind === 'history-hint') {
      return (
        <div className="msg-history-hint">
          <button
            className="msg-history-load-btn"
            onClick={() => { void handleLoadOlder() }}
            disabled={loadingOlderHistory}
          >
            {loadingOlderHistory
              ? 'Loading older messages...'
              : hiddenTimelineCount > 0
                ? `Load ${Math.min(VISIBLE_TIMELINE_STEP, hiddenTimelineCount)} older messages`
                : 'Load older messages'}
          </button>
          <span className="msg-history-meta">
            Showing latest {visibleTimelineItems.length} of {totalMessageCount ?? timelineItems.length}
          </span>
        </div>
      )
    }

    if (vItem.kind === 'work-item-runtime-card') {
      return (
        <div className="msg-row agent">
          <div className="msg-avatar agent-avatar"><IconSparkle /></div>
          <div className="msg-body">
            <WorkItemProgressCard
              workItemLog={workItemLog ?? []}
              roleWorkItems={roleWorkItems}
              executorRoleWorkItems={executorRoleWorkItems}
              childSessions={childSessions}
              isCompanyRuntime={isCompanyRuntime}
              onWorkItemClick={onWorkItemClick}
            />
          </div>
        </div>
      )
    }

    if (vItem.kind === 'progress-block') {
      return (
        <div className="msg-row agent agent-working-row">
          <div className="msg-avatar agent-avatar"><IconSparkle /></div>
          <div className="msg-body">
            <AgentProgressBlock entries={bottomProgressEntries} agentStatus={agentStatus} currentTool={currentTool} toolElapsedMs={toolElapsedMs} lastToolSummary={lastToolSummary} expandedByDefault />
          </div>
        </div>
      )
    }

    if (vItem.kind === 'pending-section') {
      return (
        <div className="msg-pending-section">
          <div className="msg-pending-header">Pending Actions</div>
          <div className="msg-pending-stack">
            {pendingCheckpointMessages.map((msg) => {
              const displayName = msg.senderDeleted ? '[Deleted]' : msg.senderName
              const color = agentColor(msg.sender === 'system' ? (msg.senderName || 'OPC') : msg.sender)
              const replyTaskId = msg.channelId.startsWith('session:')
                ? msg.channelId.slice('session:'.length)
                : (msg.metadata?.taskId ?? msg.metadata?.task_id)
              const cpType = String(msg.metadata?.checkpoint_type ?? '').trim()
              const cpReplyMeta = toCheckpointReplyMetadata(msg.metadata)

              return (
                <div key={`pending-${msg.id}`} className={`msg-row agent msg-row-pending${msg.senderDeleted ? ' deleted-sender' : ''}`}>
                  <div className="msg-avatar agent-avatar" style={{ background: color }}>
                    {displayName.charAt(0).toUpperCase()}
                  </div>
                  <div className="msg-body">
                    <div className="msg-agent-header">
                      <span className={`msg-sender${msg.senderDeleted ? ' deleted' : ''}`} style={{ color }}>{displayName}</span>
                      <span className="msg-time" title={formatFullTime(msg.timestamp)}>{formatTime(msg.timestamp)}</span>
                    </div>
                    {cpType === 'company_recruitment_confirmation' && (
                      <RecruitmentPanel
                        meta={msg.metadata!}
                        onReply={(text, extraMetadata) => onSend?.(text, replyTaskId, extraMetadata)}
                        responded={false}
                      />
                    )}
                    {cpType === 'company_staffing_selection' && (
                      <StaffingSelectionPanel
                        meta={msg.metadata!}
                        onReply={(text, extraMetadata) => onSend?.(text, replyTaskId, extraMetadata)}
                        responded={false}
                      />
                    )}
                    {cpType === 'company_work_item_gate' && (
                      <EscalationPanel meta={msg.metadata!} onReply={(text) => onSend?.(text, replyTaskId, cpReplyMeta)} responded={false} />
                    )}
                    {cpType === 'company_delivery_feedback' && (
                      <DeliveryFeedbackPanel meta={msg.metadata!} onReply={(text, extraMetadata) => onSend?.(text, replyTaskId, extraMetadata ?? cpReplyMeta)} responded={false} />
                    )}
                    {cpType === 'company_reorg_pending' && (
                      <ReorgPanel meta={msg.metadata!} onReply={(text) => onSend?.(text, replyTaskId, cpReplyMeta)} responded={false} />
                    )}
                    {cpType === 'human_escalation' && (
                      <EscalationPanel meta={msg.metadata!} onReply={(text) => onSend?.(text, replyTaskId, cpReplyMeta)} responded={false} />
                    )}
                    {cpType === 'task_user_input' && (
                      <TaskUserInputPanel
                        meta={msg.metadata!}
                        onReply={(text, extraMetadata) => onSend?.(text, replyTaskId, { ...cpReplyMeta, ...extraMetadata } as CheckpointReplyMetadata)}
                        responded={false}
                      />
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )
    }

    if (vItem.kind === 'end-anchor') {
      return <div className="msg-end-anchor" />
    }

    // Timeline item
    const { item, showDate, dateStr, isGrouped } = processed[vItem.idx]

    if (item.kind === 'progress') {
      return (
        <ProgressRow entry={item.entry} showDate={showDate} dateStr={dateStr} compact={detailMode !== 'full'} />
      )
    }

    if (item.kind === 'draft') {
      return (
        <DraftRow
          text={item.text}
          timestamp={item.timestamp}
          iteration={item.iteration}
          showDate={showDate}
          dateStr={dateStr}
        />
      )
    }

    if (item.kind === 'ops-bundle') {
      return (
        <OpsBundleRow
          events={item.events}
          showDate={showDate}
          dateStr={dateStr}
        />
      )
    }

    const { msg } = item
    const isSystem = msg.metadata?.type === 'system'
    const isExecutionContext = isExecutionContextMessage(msg)
    const isWorkItemEvent = !!(msg.metadata as any)?.is_work_item_event || msg.content.startsWith('[Company:')
    const hasCpPanel = isCheckpointCardMetadata(msg.metadata)
    const isUser = msg.sender === 'user'
    const projectUpdate = !isUser && !hasCpPanel ? parseProjectUpdatePayload(msg.content) : null

    if (isWorkItemEvent && !hasCpPanel) {
      return <WorkItemRow msg={msg} showDate={showDate} dateStr={dateStr} />
    }
    if (projectUpdate) {
      return (
        <ProjectUpdateRow
          msg={msg}
          payload={projectUpdate}
          showDate={showDate}
          dateStr={dateStr}
          isCopied={copiedId === msg.id}
          onCopy={copyMsg}
        />
      )
    }
    if (isExecutionContext && !hasCpPanel) {
      return <ContextRow msg={msg} showDate={showDate} dateStr={dateStr} />
    }
    if (isSystem && !hasCpPanel) {
      return <SystemRow msg={msg} showDate={showDate} dateStr={dateStr} />
    }
    if (isUser) {
      return (
        <UserRow
          msg={msg} showDate={showDate} dateStr={dateStr} isGrouped={isGrouped}
          isCopied={copiedId === msg.id} onCopy={copyMsg} onImageClick={setLightboxUrl}
          renderUserMarkdown={renderUserMarkdown}
        />
      )
    }
    // System-ops compaction: messages like "[Delegating to codex] ..." or
    // "[External resume] codex restored prior session →…" become a one-line
    // collapsed log row, with full content tucked behind a chevron. This
    // keeps the conversation readable instead of dumping the raw command.
    if (msg.sender === 'system') {
      const checkpointType = String(msg.metadata?.checkpoint_type ?? '').trim()
      if (!isCheckpointCardMetadata(msg.metadata)) {
        const ops = classifySystemOps(msg.content)
        if (ops) {
          return (
            <SystemOpsRow
              msg={msg} showDate={showDate} dateStr={dateStr}
              isGrouped={isGrouped} classification={ops}
            />
          )
        }
      }
    }
    return (
      <AgentRow
        msg={msg} showDate={showDate} dateStr={dateStr} isGrouped={isGrouped}
        isCopied={copiedId === msg.id}
        isCheckpointResponded={checkpointAnalysis.respondedMessageIds.has(msg.id)}
        suppressCheckpointPanel={checkpointAnalysis.duplicateMessageIds.has(msg.id)}
        onCopy={copyMsg} onSend={onSend}
      />
    )
  }, [
    processed, copiedId, copyMsg, renderUserMarkdown, checkpointAnalysis,
    onSend, handleLoadOlder, loadingOlderHistory, hiddenTimelineCount,
    visibleTimelineItems.length, totalMessageCount, timelineItems.length,
    workItemLog, roleWorkItems, executorRoleWorkItems, childSessions, messages, onWorkItemClick, onWorkItemOpenSession, bottomProgressEntries,
    agentStatus, currentTool, toolElapsedMs, lastToolSummary,
    pendingCheckpointMessages, showProgressBlock, detailMode,
  ])

  if (timelineItems.length === 0 && pendingCheckpointMessages.length === 0) {
    return (
      <div className="msg-list" ref={listRef} onScroll={handleScroll}>
        <div className="msg-welcome">
          <div className="msg-welcome-icon">{welcome.icon}</div>
          <div className="msg-welcome-title">{channelName || welcome.title}</div>
          <div className="msg-welcome-hint">{welcome.hint}</div>
        </div>
        {hasWorkItemRuntimeCard && (
          <div className="msg-row agent">
            <div className="msg-avatar agent-avatar"><IconSparkle /></div>
            <div className="msg-body">
              <WorkItemProgressCard
                workItemLog={workItemLog ?? []}
                roleWorkItems={roleWorkItems}
                executorRoleWorkItems={executorRoleWorkItems}
                childSessions={childSessions}
                isCompanyRuntime={isCompanyRuntime}
                onWorkItemClick={onWorkItemClick}
              />
            </div>
          </div>
        )}
        {showProgressBlock && (
          <div className="msg-row agent">
            <div className="msg-avatar agent-avatar"><IconSparkle /></div>
            <div className="msg-body">
              <AgentProgressBlock entries={bottomProgressEntries} agentStatus={agentStatus} currentTool={currentTool} toolElapsedMs={toolElapsedMs} lastToolSummary={lastToolSummary} expandedByDefault />
            </div>
          </div>
        )}
        <div className="msg-end-anchor" />
      </div>
    )
  }

  const virtualRows = virtualizer.getVirtualItems()

  return (
    <div className="msg-list" ref={listRef} onScroll={handleScroll}>
      {/* Spacer before visible items — pushes content down to correct scroll position */}
      <div ref={contentSpacerRef} style={{ height: virtualizer.getTotalSize(), width: '100%', position: 'relative' }}>
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            transform: `translateY(${virtualRows[0]?.start ?? 0}px)`,
          }}
        >
          {virtualRows.map((virtualRow) => (
            <div
              key={virtualRow.key}
              data-index={virtualRow.index}
              ref={virtualizer.measureElement}
            >
              {renderVirtualRow(virtualItems[virtualRow.index])}
            </div>
          ))}
        </div>
      </div>

      {lightboxUrl && (
        <div className="lightbox-overlay" onClick={() => setLightboxUrl(null)}>
          <img className="lightbox-img" src={lightboxUrl} alt="Preview" />
        </div>
      )}
    </div>
  )
})
