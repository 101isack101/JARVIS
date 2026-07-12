export type JarvisState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'blocked'
export type JarvisMode = 'PTT' | 'LIBRE'
export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'
export type LogLevel = 'ok' | 'warn' | 'error' | 'info'
export type ConversationRole = 'user' | 'jarvis'

export interface ConversationMessage {
  id: string
  role: ConversationRole
  text: string
  stamp: string
}

export interface LogEvent {
  stamp: string
  level: LogLevel
  message: string
}

export interface ProviderBudget {
  provider: string
  spentUsd: number
  limitUsd: number
  pct: number
  status: 'ok' | 'warn' | 'alert' | 'blocked'
  blocked: boolean
  tokens: number
  tokensLabel: string
  label: string
}

export interface BudgetPayload {
  period: string
  hardStop: boolean
  gemini: ProviderBudget
  claude: ProviderBudget
  totalUsd: number
}

export interface MemoryStats {
  ok: number
  active: number
  error: number
}

export interface AgentToolEvent {
  id: string
  stamp: string
  name: string
  summary: string
  detail: string
  elapsedMs: number | null
  startedAt?: number
  endedAt?: number | null
  status: 'running' | 'ok' | 'error'
}

export interface AudioTelemetry {
  erlePeakDb?: number
  wakewordPeak?: number
  stamp?: string
}

export type MissionStatus =
  | 'queued' | 'planning' | 'ready' | 'running' | 'verifying'
  | 'cancel_requested' | 'recovering'
  | 'completed' | 'cancelled' | 'blocked' | 'failed'

export interface MissionSummary {
  id: string
  goal: string
  status: MissionStatus
  autonomy_level: 'L1' | string
  created_at: string
  updated_at: string
  finished_at: string | null
  completed_steps: number
  total_steps: number
  error_code: string | null
  final_summary: string | null
}

export interface MissionEvent {
  id: number | null
  mission_id: string
  step_id: string | null
  event_type: string
  level: LogLevel
  payload: Record<string, unknown>
  created_at: string
}

export interface ProviderRuntimeTelemetry {
  tokens: number
  tokensLabel: string
  costUsd: number
  tokensPerMin: number
  usdPerMin: number
}

export interface CameraRuntimeTelemetry {
  active: boolean
  streamFps: number
  streamKbps: number
  previewFps: number
  previewKbps: number
  streamFrames: number
  previewFrames: number
  streamMb: number
  previewMb: number
  avgFrameKb: number
}

export interface RuntimeTelemetry {
  stamp: string
  windowS: number
  cameraWindowS: number
  totalTokens: number
  totalTokensLabel: string
  totalUsd: number
  totalTokensPerMin: number
  totalUsdPerMin: number
  gemini: ProviderRuntimeTelemetry
  claude: ProviderRuntimeTelemetry
  other: ProviderRuntimeTelemetry
  camera: CameraRuntimeTelemetry
}

export interface SystemStats {
  cpu: number
  ram: number
  ramUsedGb: number
  ramTotalGb: number
  diskUsedGb: number
  diskTotalGb: number
  diskPct: number
}

export interface Weather {
  tempC: number
  feelsC: number
  humidity: number
  windMs: number
  place: string
  desc: string
  code: number
}

export interface ApprovalPayload {
  id: string
  risk: 'low' | 'medium' | 'high'
  title: string
  details: string
  timeout_s: number
}

export interface JarvisSnapshot {
  version: string
  uiToken: string
  state: JarvisState
  mode: JarvisMode
  connection: { status: ConnectionStatus; detail: string }
  privacy: string
  inputTranscript: string
  outputTranscript: string
  conversationMessages?: ConversationMessage[]
  events: LogEvent[]
  memory: MemoryStats
  budget: BudgetPayload
  agentEvents: AgentToolEvent[]
  missions?: MissionSummary[]
  missionEvents?: MissionEvent[]
  audioTelemetry: AudioTelemetry | null
  runtimeTelemetry?: RuntimeTelemetry | null
  latency: string[]
  cameraActive?: boolean
  cameraFrame?: string | null
  cameraFocus?: { box: unknown; label: string } | null
  systemStats?: SystemStats | null
  weather?: Weather | null
}
