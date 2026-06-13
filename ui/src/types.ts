export type JarvisState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'blocked'
export type JarvisMode = 'PTT' | 'LIBRE'
export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected'
export type LogLevel = 'ok' | 'warn' | 'error' | 'info'

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
  name: string
  args: Record<string, unknown>
  startedAt: number
  endedAt?: number
  status: 'running' | 'done' | 'error'
}

export interface AudioTelemetry {
  erlePeakDb: number
  wakewordPeak: number
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
  events: LogEvent[]
  memory: MemoryStats
  budget: BudgetPayload
}
