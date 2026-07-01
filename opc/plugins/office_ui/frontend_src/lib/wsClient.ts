import type {
  AgentRuntimePayload,
  EmployeeDetailPayload,
  KanbanViewDataPayload,
  OrgInfoPayload,
  OrgCreateMemberInput,
  OrgSavedCreatePayload,
  ReorgListPayload,
  SavedOrgSummary,
  SessionProgressPayload,
  SocketEnvelope,
  SocketStatus,
  TalentListPayload,
  VisualEvent,
  VisualSnapshot,
  WorkerNotificationPayload,
  WorkItemProgressPayload,
} from '../types/visual'
import type { CheckpointReplyMetadata, OutgoingAttachmentPayload } from '../types/chat'
import type { TaskPreferredAgent } from '../types/kanban'

interface SocketHandlers {
  onSnapshot?: (snapshot: VisualSnapshot) => void
  onEvent?: (event: VisualEvent) => void
  onAck?: (payload: Record<string, unknown>) => void
  onStatus?: (status: SocketStatus, detail?: string) => void
  onChannelCreated?: (payload: { channel_id: string; name: string; channel_type: string; participants: string[] }) => void
  onBoardEvent?: (payload: Record<string, unknown>) => void
  onCrossOfficeCollab?: (payload: { agent_ids: string[]; task_id: string; action: string }) => void
  onCollabMessage?: (type: string, payload: Record<string, unknown>) => void
  onAgentRuntimeUpdate?: (payload: AgentRuntimePayload) => void
  onWorkerNotification?: (payload: WorkerNotificationPayload) => void
  onKanbanViewData?: (payload: KanbanViewDataPayload) => void
  onSessionCreated?: (payload: { project_id: string; task_id: string; channel_id: string; session_id?: string; parent_session_id?: string; origin_task_id?: string; title: string; status: string; created_at: number; assignee_ids?: string[]; exec_mode?: string; company_profile?: string; org_id?: string; organization_id?: string; preferred_agent?: TaskPreferredAgent; selected_execution_agent?: TaskPreferredAgent }) => void
  onSessionUpdated?: (payload: { project_id: string; task_id: string; exec_mode?: string; company_profile?: string; org_id?: string; organization_id?: string; preferred_agent?: TaskPreferredAgent; selected_execution_agent?: TaskPreferredAgent }) => void
  onSessionMessage?: (payload: Record<string, unknown>) => void
  onSessionTitleUpdated?: (payload: { project_id: string; task_id: string; title: string }) => void
  onSessionDeleted?: (payload: { project_id: string; task_id: string }) => void
  onSessionProgress?: (payload: SessionProgressPayload) => void
  onChildSessionCreated?: (payload: { project_id: string; session_id: string; parent_session_id: string; task_id: string; origin_task_id?: string; title: string; agent_id?: string; org_id?: string; organization_id?: string; selected_execution_agent?: TaskPreferredAgent }) => void
  onProjectSwitched?: (payload: { project_id: string; switch_seq?: string }) => void
  onProjectDeleted?: (payload: { project_id: string }) => void
  onOrgInfo?: (payload: OrgInfoPayload) => void
  onRecoveryStatus?: (payload: any) => void
  onRecoveryResult?: (payload: any) => void
  onTalentList?: (payload: TalentListPayload) => void
  onTalentScanLocal?: (payload: { templates: Array<{ template_id: string; name: string; description: string; category: string; domains: string[]; tags: string[] }> }) => void
  onEmployeeDetail?: (payload: EmployeeDetailPayload) => void
  onReorgList?: (payload: ReorgListPayload) => void
  onWorkItemProgress?: (payload: WorkItemProgressPayload) => void
  onMarketListInstalled?: (payload: { packages: Array<Record<string, unknown>> }) => void
  onMarketBrowse?: (payload: { presets: Array<Record<string, unknown>> }) => void
  onMarketPreview?: (payload: Record<string, unknown>) => void
  onOrgConfigExport?: (payload: { yaml: string }) => void
  onOrgConfigImport?: (payload: { ok: boolean; dry_run?: boolean; preview?: { roles_added: number; roles_removed: number; employees_changed: number }; error?: string; validation_errors?: string[] }) => void
  onOrgSavedList?: (payload: { orgs: SavedOrgSummary[]; active_name?: string | null }) => void
  onOrgSavedSaveAs?: (payload: { ok: boolean; name: string; error?: string }) => void
  onOrgSavedCreate?: (payload: OrgSavedCreatePayload) => void
  onOrgSavedLoad?: (payload: { ok: boolean; name: string; error?: string }) => void
  onOrgSavedDelete?: (payload: { ok: boolean; name: string; error?: string }) => void
  onCommsState?: (payload: CommsStatePayload) => void
  onCommsMessage?: (payload: CommsMessagePayload) => void
}

export interface CommsMessageItem {
  message_id: string
  from: string
  to?: string
  subject: string
  sent_at: string
  blocking: boolean
  path: string
  bucket?: 'new' | 'seen' | 'sent'
}

/** @deprecated Use CommsMessageItem instead */
export type CommsRecentUnread = CommsMessageItem

export interface CommsRolePayload {
  role_id: string
  unread_count: number
  has_blocking: boolean
  seen_count: number
  outbox_count: number
  recent_unread: CommsMessageItem[]
  recent_seen?: CommsMessageItem[]
  recent_outbox?: CommsMessageItem[]
}

export interface CommsMeetingPayload {
  meeting_id: string
  topic: string
  status: string
  organizer: string
  participants: string[]
  entry_count: number
  opened_at: string
  closed_at?: string | null
  decision?: string | null
  transcript_path: string
}

export interface CommsFailurePayload {
  operation: string
  from_role: string
  to_role: string
  reason: string
  attempted_path?: string
  attempted_command?: string
  recorded_at?: string
  attempt_count?: number
  can_retry?: boolean
}

export interface CommsStatePayload {
  available: boolean
  reason?: string
  empty?: boolean
  project_id?: string
  session_id?: string
  workspace_root?: string
  output_root?: string
  comms_root?: string
  projection_status?: string
  recent_failures?: CommsFailurePayload[]
  roles?: CommsRolePayload[]
  meetings?: CommsMeetingPayload[]
}

export interface CommsMessagePayload {
  project_id: string
  path: string
  header: {
    from?: string
    to?: string
    sent_at?: string
    blocking?: boolean
    [key: string]: unknown
  }
  body: string
}

const RECONNECT_BASE_MS = 2000
const RECONNECT_MAX_MS = 30000
const RECONNECT_MAX_ATTEMPTS = 20
const PENDING_QUEUE_MAX = 100
const HEARTBEAT_INTERVAL_MS = 30_000
const HEARTBEAT_TIMEOUT_MS = 10_000
const PROJECT_SCOPED_MESSAGE_TYPES = new Set([
  'collab_sync',
  'kanban_create_board',
  'kanban_create_task',
  'kanban_update_task',
  'kanban_move_task',
  'kanban_delete_board',
  'kanban_delete_task',
  'kanban_assign',
  'kanban_status',
  'kanban_switch_view',
  'run_task',
  'create_session',
  'session_send',
  'session_update_config',
  'session_delete',
  'session_detail',
  'session_stop',
  'session_resume',
  'session_complete',
  'session_update_title',
  'secretary_send',
  'project_index',
  'recovery_action',
  'comms_state',
  'comms_read_message',
])

export class VisualSocketClient {
  private ws: WebSocket | null = null
  private reconnectTimer: number | null = null
  private closedByUser = false
  private reconnectAttempt = 0
  private pendingQueue: string[] = []
  private heartbeatTimer: number | null = null
  private pongTimer: number | null = null

  constructor(
    private url: string,
    private handlers: SocketHandlers,
  ) {}

  updateUrl(url: string): void {
    this.url = url
  }

  connect(): void {
    this.closedByUser = false
    this.handlers.onStatus?.('connecting')

    this.ws = new WebSocket(this.url)
    this.ws.onopen = () => {
      this.reconnectAttempt = 0
      this.handlers.onStatus?.('connected')
      this.flushPendingQueue()
      this.startHeartbeat()
    }
    this.ws.onmessage = (evt) => {
      this.handleMessage(evt.data)
    }
    this.ws.onerror = () => {
      this.handlers.onStatus?.('error', 'WebSocket error')
    }
    this.ws.onclose = () => {
      this.stopHeartbeat()
      this.handlers.onStatus?.('disconnected')
      this.ws = null
      if (!this.closedByUser) {
        this.scheduleReconnect()
      }
    }
  }

  disconnect(): void {
    this.closedByUser = true
    this.stopHeartbeat()
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
    this.ws = null
  }

  send(payload: Record<string, unknown>): void {
    if (!this.ensureProjectScope(payload)) {
      return
    }
    const data = JSON.stringify(payload)
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      if (this.pendingQueue.length < PENDING_QUEUE_MAX) {
        this.pendingQueue.push(data)
      }
      return
    }
    this.ws.send(data)
  }

  // ── Agent management ───────────────────────────────────────────────────

  createAgent(role: Record<string, unknown>): void {
    this.send({ type: 'create_agent', role })
  }

  deleteAgent(agentId: string): void {
    this.send({ type: 'delete_agent', agent_id: agentId })
  }

  moveAgent(agentId: string, officeId: string, seatZone?: string): void {
    this.send({ type: 'move_agent', agent_id: agentId, office_id: officeId, seat_zone: seatZone })
  }

  listAgents(): void {
    this.send({ type: 'list_agents' })
  }

  createFromTemplate(templateId: string, name?: string): void {
    this.send({ type: 'create_agent', role: { id: templateId, template: templateId, name: name ?? templateId } })
  }

  // ── Execution mode ─────────────────────────────────────────────────────

  setExecutionMode(mode: string, profile?: string, preferredAgent?: TaskPreferredAgent, orgId?: string): void {
    this.send({ type: 'set_execution_mode', mode, profile: profile ?? 'corporate', preferred_agent: preferredAgent, org_id: orgId })
  }

  // ── Kanban integration ─────────────────────────────────────────────────

  assignTaskToAgent(projectId: string, taskId: string, agentId: string, taskTitle: string): void {
    const pid = this.requireProjectId(projectId, 'kanban_assign')
    this.send({ type: 'kanban_assign', task_id: taskId, agent_id: agentId, task_title: taskTitle, project_id: pid })
  }

  updateTaskStatus(projectId: string, taskId: string, status: string): void {
    const pid = this.requireProjectId(projectId, 'kanban_status')
    this.send({ type: 'kanban_status', task_id: taskId, status, project_id: pid })
  }

  kanbanCreateTask(opts: {
    board_id: string; column_id: string; title: string;
    description?: string; priority?: string;
    assignee_ids?: string[]; tags?: string[];
    task_id?: string;
    project_id: string;
  }): void {
    const pid = this.requireProjectId(opts.project_id, 'kanban_create_task')
    this.send({ type: 'kanban_create_task', ...opts, project_id: pid })
  }

  kanbanUpdateTask(projectId: string, taskId: string, updates: Record<string, unknown>): void {
    const pid = this.requireProjectId(projectId, 'kanban_update_task')
    this.send({ type: 'kanban_update_task', task_id: taskId, updates, project_id: pid })
  }

  kanbanMoveTask(projectId: string, taskId: string, columnId: string, sortOrder = 0): void {
    const pid = this.requireProjectId(projectId, 'kanban_move_task')
    this.send({ type: 'kanban_move_task', task_id: taskId, column_id: columnId, sort_order: sortOrder, project_id: pid })
  }

  kanbanDeleteBoard(projectId: string, boardId: string): void {
    const pid = this.requireProjectId(projectId, 'kanban_delete_board')
    this.send({ type: 'kanban_delete_board', project_id: pid, board_id: boardId })
  }

  kanbanDeleteTask(projectId: string, taskId: string): void {
    const pid = this.requireProjectId(projectId, 'kanban_delete_task')
    this.send({ type: 'kanban_delete_task', task_id: taskId, project_id: pid })
  }

  kanbanSwitchView(projectId: string, level: 'global' | 'office' | 'agent', targetId?: string): void {
    const pid = this.requireProjectId(projectId, 'kanban_switch_view')
    this.send({ type: 'kanban_switch_view', level, target_id: targetId, project_id: pid })
  }

  getAgentDetail(agentId: string): void {
    this.send({ type: 'get_agent_detail', agent_id: agentId })
  }

  // ── Collaboration protocol ─────────────────────────────────────────────

  collabSync(projectId: string, switchSeq?: string, viewGeneration?: number): void {
    const pid = this.requireProjectId(projectId, 'collab_sync')
    this.send({ type: 'collab_sync', project_id: pid, switch_seq: switchSeq, view_generation: viewGeneration })
  }

  projectIndex(projectId: string, switchSeq?: string, viewGeneration?: number): void {
    const pid = this.requireProjectId(projectId, 'project_index')
    this.send({ type: 'project_index', project_id: pid, switch_seq: switchSeq, view_generation: viewGeneration })
  }

  // ── Session protocol ───────────────────────────────────────────────────

  createSession(projectId: string, title?: string, execMode?: string, companyProfile?: string, preferredAgent?: TaskPreferredAgent, orgId?: string): void {
    const pid = this.requireProjectId(projectId, 'create_session')
    this.send({
      type: 'create_session',
      project_id: pid,
      title: title ?? 'New Chat',
      exec_mode: execMode,
      company_profile: companyProfile,
      preferred_agent: preferredAgent,
      org_id: orgId,
    })
  }

  sessionSend(
    projectId: string,
    taskId: string,
    content: string,
    attachments?: OutgoingAttachmentPayload[],
    metadata?: CheckpointReplyMetadata,
  ): void {
    const pid = this.requireProjectId(projectId, 'session_send')
    this.send({
      type: 'session_send',
      project_id: pid,
      task_id: taskId,
      content,
      attachments: attachments ?? [],
      metadata,
    })
  }

  deleteSession(projectId: string, taskId: string): void {
    const pid = this.requireProjectId(projectId, 'session_delete')
    this.send({ type: 'session_delete', project_id: pid, task_id: taskId })
  }

  sessionUpdateTitle(projectId: string, taskId: string, title: string): void {
    const pid = this.requireProjectId(projectId, 'session_update_title')
    this.send({ type: 'session_update_title', project_id: pid, task_id: taskId, title })
  }

  sessionUpdateConfig(projectId: string, taskId: string, execMode: string, companyProfile?: string, preferredAgent?: TaskPreferredAgent, orgId?: string): void {
    const pid = this.requireProjectId(projectId, 'session_update_config')
    this.send({
      type: 'session_update_config',
      project_id: pid,
      task_id: taskId,
      exec_mode: execMode,
      company_profile: companyProfile,
      preferred_agent: preferredAgent,
      org_id: orgId,
    })
  }

  sessionStop(projectId: string, taskId: string): void {
    const pid = this.requireProjectId(projectId, 'session_stop')
    this.send({ type: 'session_stop', project_id: pid, task_id: taskId })
  }

  sessionResume(projectId: string, taskId: string, content?: string): void {
    const pid = this.requireProjectId(projectId, 'session_resume')
    this.send({ type: 'session_resume', project_id: pid, task_id: taskId, content })
  }

  sessionComplete(projectId: string, taskId: string): void {
    const pid = this.requireProjectId(projectId, 'session_complete')
    this.send({ type: 'session_complete', project_id: pid, task_id: taskId })
  }

  sessionDetail(
    projectId: string,
    taskId: string,
    opts?: { limit?: number; beforeCreatedAt?: number; beforeMessageId?: string; detailLevel?: 'summary' | 'full'; include?: string[]; viewGeneration?: number },
  ): void {
    const pid = this.requireProjectId(projectId, 'session_detail')
    this.send({
      type: 'session_detail',
      project_id: pid,
      task_id: taskId,
      limit: opts?.limit,
      before_created_at: opts?.beforeCreatedAt,
      before_message_id: opts?.beforeMessageId,
      detail_level: opts?.detailLevel,
      include: opts?.include,
      view_generation: opts?.viewGeneration,
    })
  }

  secretarySend(projectId: string, content: string): void {
    const pid = this.requireProjectId(projectId, 'secretary_send')
    this.send({ type: 'secretary_send', project_id: pid, content })
  }

  // ── Project management ──────────────────────────────────────────────

  listProjects(): void {
    this.send({ type: 'list_projects' })
  }

  createProject(projectId: string): void {
    this.send({ type: 'create_project', project_id: this.normalizeProjectId(projectId) })
  }

  deleteProject(projectId: string): void {
    this.send({ type: 'delete_project', project_id: this.normalizeProjectId(projectId) })
  }

  switchProject(projectId: string, switchSeq?: string): void {
    this.send({ type: 'switch_project', project_id: this.normalizeProjectId(projectId), switch_seq: switchSeq })
  }

  // ── Org info ──────────────────────────────────────────────────────────

  orgInfo(): void {
    this.send({ type: 'org_info' })
  }

  // ── Phase 4: Talent Market, Employee Detail, Reorg ───────────────────

  talentImport(repoPath: string): void {
    this.send({ type: 'talent_import', repo_path: repoPath })
  }

  talentList(): void {
    this.send({ type: 'talent_list' })
  }

  talentScanLocal(): void {
    this.send({ type: 'talent_scan_local' })
  }

  talentImportSelected(templateIds: string[]): void {
    this.send({ type: 'talent_import_selected', template_ids: templateIds })
  }

  talentHire(templateId: string, roleId: string, employeeName?: string, orgId?: string): void {
    this.send({ type: 'talent_hire', template_id: templateId, role_id: roleId, employee_name: employeeName, org_id: orgId })
  }

  employeeDetail(employeeId: string): void {
    this.send({ type: 'employee_detail', employee_id: employeeId })
  }

  reorgList(): void {
    this.send({ type: 'reorg_list' })
  }

  reorgDecide(proposalId: string, approved: boolean, notes?: string): void {
    this.send({ type: 'reorg_decide', proposal_id: proposalId, approved, notes })
  }

  importEmployeeAsAgent(employeeId: string, officeId?: string): void {
    this.send({ type: 'import_employee_as_agent', employee_id: employeeId, office_id: officeId })
  }

  // ── OPC Market ─────────────────────────────────────────────────────────

  marketBrowse(): void {
    this.send({ type: 'market_browse' })
  }

  marketPreview(presetId: string): void {
    this.send({ type: 'market_preview', preset_id: presetId })
  }

  marketApplyPreset(presetId: string, strategy: string = 'namespace'): void {
    this.send({ type: 'market_apply_preset', preset_id: presetId, strategy })
  }

  marketListInstalled(): void {
    this.send({ type: 'market_list_installed' })
  }

  marketExport(data: { package_id: string; name: string; description: string; version: string }): void {
    this.send({ type: 'market_export', ...data })
  }

  marketInstall(path: string, strategy: string = 'namespace'): void {
    this.send({ type: 'market_install', path, strategy })
  }

  marketUninstall(packageId: string): void {
    this.send({ type: 'market_uninstall', package_id: packageId })
  }

  // ── Org Editing ───────────────────────────────────────────────────────

  addRole(roleId: string, name: string, responsibility: string, reportsTo: string = 'owner', icon?: string | null): void {
    this.send({ type: 'add_role', role_id: roleId, name, responsibility, reports_to: reportsTo, icon: icon || null })
  }

  bulkAddRoles(roles: Array<{ role_id: string; name: string; responsibility: string; reports_to: string; icon?: string | null }>): void {
    this.send({ type: 'bulk_add_roles', roles })
  }

  updateRole(roleId: string, updates: {
    name?: string
    responsibility?: string
    reports_to?: string
    can_spawn?: string[]
    icon?: string | null
    execution_strategy?: string
    preferred_external_agent?: string | null
    prompt_refs?: string[]
    tools?: string[]
  }): void {
    this.send({ type: 'update_role', role_id: roleId, ...updates })
  }

  deleteRole(roleId: string): void {
    this.send({ type: 'delete_role', role_id: roleId })
  }

  updateRuntimePolicy(policy: Record<string, any>): void {
    this.send({ type: 'update_runtime_policy', policy })
  }

  updateOrgStrategy(data: { final_decider_role_id?: string | null }): void {
    this.send({ type: 'update_org_strategy', ...data })
  }

  resetArchitecture(): void {
    this.send({ type: 'reset_architecture' })
  }

  orgConfigExport(): void {
    this.send({ type: 'org_config_export' })
  }

  orgConfigImport(yaml: string, dryRun: boolean): void {
    this.send({ type: 'org_config_import', yaml, dry_run: dryRun })
  }

  orgSavedList(): void {
    this.send({ type: 'org_saved_list' })
  }

  orgSavedSaveAs(name: string, overwrite: boolean): void {
    this.send({ type: 'org_saved_save_as', name, overwrite })
  }

  orgSavedCreate(organizationName: string, members: OrgCreateMemberInput[]): void {
    this.send({ type: 'org_saved_create', organization_name: organizationName, members })
  }

  orgSavedLoad(name: string): void {
    this.send({ type: 'org_saved_load', name })
  }

  orgSavedDelete(name: string): void {
    this.send({ type: 'org_saved_delete', name })
  }

  recoveryAction(projectId: string, action: 'resume' | 'cancel' | 'scan', parentTaskId?: string): void {
    const pid = this.requireProjectId(projectId, 'recovery_action')
    this.send({ type: 'recovery_action', project_id: pid, action, parent_task_id: parentTaskId })
  }

  commsState(projectId: string, opts?: { task_id?: string; session_id?: string }): void {
    const pid = this.requireProjectId(projectId, 'comms_state')
    this.send({ type: 'comms_state', project_id: pid, ...(opts || {}) })
  }

  commsReadMessage(projectId: string, path: string): void {
    const pid = this.requireProjectId(projectId, 'comms_read_message')
    this.send({ type: 'comms_read_message', project_id: pid, path })
  }

  // ── Internal ───────────────────────────────────────────────────────────

  private normalizeProjectId(value: unknown): string {
    return typeof value === 'string' ? value.trim() : ''
  }

  private requireProjectId(projectId: unknown, action: string): string {
    const pid = this.normalizeProjectId(projectId)
    if (!pid) {
      throw new Error(`${action} requires non-empty project_id`)
    }
    return pid
  }

  private ensureProjectScope(payload: Record<string, unknown>): boolean {
    const messageType = typeof payload.type === 'string' ? payload.type : ''
    if (!PROJECT_SCOPED_MESSAGE_TYPES.has(messageType)) {
      return true
    }
    const pid = this.normalizeProjectId(payload.project_id ?? payload.projectId)
    if (!pid) {
      const error = `${messageType} requires non-empty project_id`
      console.error(`[wsClient] ${error}`, payload)
      this.handlers.onAck?.({ ok: false, error, action: messageType })
      return false
    }
    payload.project_id = pid
    return true
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== 'string') {
      return
    }
    let parsed: SocketEnvelope | null = null
    try {
      parsed = JSON.parse(raw) as SocketEnvelope
    } catch {
      return
    }
    if (!parsed || typeof parsed !== 'object' || !('type' in parsed)) {
      return
    }
    try { switch (parsed.type) {
      case 'snapshot':
        this.handlers.onSnapshot?.(parsed.payload)
        break
      case 'event':
        this.handlers.onEvent?.(parsed.payload)
        break
      case 'ack':
        this.handlers.onAck?.(parsed.payload)
        break
      case 'channel_created':
        this.handlers.onChannelCreated?.(parsed.payload)
        break
      case 'board_task_created':
      case 'board_task_moved':
        this.handlers.onBoardEvent?.(parsed.payload as unknown as Record<string, unknown>)
        break
      case 'board_task_status_changed':
      case 'execution_mode_resolved':
      case 'project_run_updated':
      case 'seat_digest_updated':
      case 'work_item_batch_updated':
      case 'session_runtime_control':
        this.handlers.onCollabMessage?.(parsed.type, parsed.payload as Record<string, unknown>)
        break
      case 'cross_office_collab':
        this.handlers.onCrossOfficeCollab?.(parsed.payload)
        break
      case 'chat_new_message':
      case 'chat_channel_created':
      case 'kanban_updated':
      case 'kanban_board_created':
      case 'collab_sync_push':
      case 'project_index_push':
        this.handlers.onCollabMessage?.(parsed.type, parsed.payload as Record<string, unknown>)
        break
      case 'agent_runtime_update':
        this.handlers.onAgentRuntimeUpdate?.(parsed.payload)
        break
      case 'worker_notification':
        this.handlers.onWorkerNotification?.(parsed.payload as WorkerNotificationPayload)
        break
      case 'session_progress':
        this.handlers.onSessionProgress?.(parsed.payload)
        break
      case 'work_item_progress':
        this.handlers.onWorkItemProgress?.(parsed.payload as unknown as WorkItemProgressPayload)
        break
      case 'kanban_view_data':
        this.handlers.onKanbanViewData?.(parsed.payload)
        break
      case 'session_created':
        this.handlers.onSessionCreated?.(parsed.payload)
        break
      case 'session_updated':
        this.handlers.onSessionUpdated?.(parsed.payload)
        break
      case 'session_message':
        this.handlers.onSessionMessage?.(parsed.payload as Record<string, unknown>)
        break
      case 'session_title_updated':
        this.handlers.onSessionTitleUpdated?.(parsed.payload)
        break
      case 'session_deleted':
        this.handlers.onSessionDeleted?.(parsed.payload)
        break
      case 'child_session_created':
        this.handlers.onChildSessionCreated?.(parsed.payload)
        break
      case 'project_switched':
        this.handlers.onProjectSwitched?.(parsed.payload)
        break
      case 'project_deleted':
        this.handlers.onProjectDeleted?.(parsed.payload)
        break
      case 'org_info':
        this.handlers.onOrgInfo?.(parsed.payload)
        break
      case 'comms_state':
        this.handlers.onCommsState?.(parsed.payload as unknown as CommsStatePayload)
        break
      case 'comms_message':
        this.handlers.onCommsMessage?.(parsed.payload as unknown as CommsMessagePayload)
        break
      case 'comms_state_dirty':
        // Server pushed a "something changed" hint after a comms message
        // was sent. Re-issue the snapshot request so the panel updates
        // immediately instead of waiting for its polling tick.
        try {
          const projectId = typeof parsed.payload?.project_id === 'string' ? parsed.payload.project_id : ''
          if (projectId) this.commsState(projectId)
        } catch { /* ignore */ }
        break
      case 'recovery_status':
        this.handlers.onRecoveryStatus?.(parsed.payload)
        break
      case 'recovery_result':
        this.handlers.onRecoveryResult?.(parsed.payload)
        break
      case 'talent_list':
        this.handlers.onTalentList?.(parsed.payload)
        break
      case 'talent_scan_local':
        this.handlers.onTalentScanLocal?.(parsed.payload)
        break
      case 'employee_detail':
        this.handlers.onEmployeeDetail?.(parsed.payload)
        break
      case 'reorg_list':
        this.handlers.onReorgList?.(parsed.payload)
        break
      case 'market_list_installed':
        this.handlers.onMarketListInstalled?.(parsed.payload as unknown as { packages: Array<Record<string, unknown>> })
        break
      case 'market_browse':
        this.handlers.onMarketBrowse?.(parsed.payload as unknown as { presets: Array<Record<string, unknown>> })
        break
      case 'market_preview':
        this.handlers.onMarketPreview?.(parsed.payload as Record<string, unknown>)
        break
      case 'org_config_export':
        this.handlers.onOrgConfigExport?.(parsed.payload as { yaml: string })
        break
      case 'org_config_import':
        this.handlers.onOrgConfigImport?.(parsed.payload as any)
        break
      case 'org_saved_list':
        this.handlers.onOrgSavedList?.(parsed.payload as { orgs: SavedOrgSummary[]; active_name?: string | null })
        break
      case 'org_saved_save_as':
        this.handlers.onOrgSavedSaveAs?.(parsed.payload as { ok: boolean; name: string; error?: string })
        break
      case 'org_saved_create':
        this.handlers.onOrgSavedCreate?.(parsed.payload as OrgSavedCreatePayload)
        break
      case 'org_saved_load':
        this.handlers.onOrgSavedLoad?.(parsed.payload as { ok: boolean; name: string; error?: string })
        break
      case 'org_saved_delete':
        this.handlers.onOrgSavedDelete?.(parsed.payload as { ok: boolean; name: string; error?: string })
        break
      case 'pong':
        this.handlePong()
        break
      default:
        break
    }
    } catch (e) { console.error('[wsClient] Error handling message:', parsed.type, e) }
  }

  private flushPendingQueue(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return
    const queued = this.pendingQueue.splice(0)
    for (const data of queued) {
      this.ws.send(data)
    }
  }

  private startHeartbeat(): void {
    this.stopHeartbeat()
    this.heartbeatTimer = window.setInterval(() => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return
      this.ws.send(JSON.stringify({ type: 'ping' }))
      this.pongTimer = window.setTimeout(() => {
        this.pongTimer = null
        this.ws?.close()
      }, HEARTBEAT_TIMEOUT_MS)
    }, HEARTBEAT_INTERVAL_MS)
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      window.clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
    if (this.pongTimer !== null) {
      window.clearTimeout(this.pongTimer)
      this.pongTimer = null
    }
  }

  private handlePong(): void {
    if (this.pongTimer !== null) {
      window.clearTimeout(this.pongTimer)
      this.pongTimer = null
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return
    if (this.reconnectAttempt >= RECONNECT_MAX_ATTEMPTS) {
      this.handlers.onStatus?.('error', 'max reconnect attempts reached')
      return
    }
    const delay = Math.min(
      RECONNECT_BASE_MS * Math.pow(2, this.reconnectAttempt),
      RECONNECT_MAX_MS,
    )
    this.reconnectAttempt++
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }
}
