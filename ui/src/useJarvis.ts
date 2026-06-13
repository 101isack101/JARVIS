import { useEffect, useRef, useState, useCallback } from 'react'
import type {
  JarvisState, JarvisMode, ConnectionStatus, LogEvent,
  BudgetPayload, MemoryStats, AgentToolEvent, AudioTelemetry,
  ApprovalPayload, JarvisSnapshot,
} from './types'

export interface JarvisUIState {
  version: string
  uiToken: string
  state: JarvisState
  mode: JarvisMode
  connectionStatus: ConnectionStatus
  connectionDetail: string
  inputTranscript: string
  outputTranscript: string
  events: LogEvent[]
  memory: MemoryStats
  budget: BudgetPayload | null
  agentTools: AgentToolEvent[]
  audioTelemetry: AudioTelemetry | null
  latencyLines: string[]
  pendingApproval: ApprovalPayload | null
  cameraActive: boolean
  cameraFrame: string | null        // base64 JPEG
  cameraFocus: { box: unknown; label: string } | null
}

const INITIAL: JarvisUIState = {
  version: '',
  uiToken: '',
  state: 'idle',
  mode: 'PTT',
  connectionStatus: 'connecting',
  connectionDetail: '',
  inputTranscript: '',
  outputTranscript: '',
  events: [],
  memory: { ok: 0, active: 0, error: 0 },
  budget: null,
  agentTools: [],
  audioTelemetry: null,
  latencyLines: [],
  pendingApproval: null,
  cameraActive: false,
  cameraFrame: null,
  cameraFocus: null,
}

type Dispatch = React.Dispatch<React.SetStateAction<JarvisUIState>>

function applyCommand(set: Dispatch, command: string, args: unknown[]) {
  set(prev => {
    switch (command) {
      case 'snapshot': {
        const snap = args[0] as JarvisSnapshot
        return {
          ...prev,
          version: snap.version,
          uiToken: snap.uiToken,
          state: snap.state,
          mode: snap.mode,
          connectionStatus: snap.connection.status,
          connectionDetail: snap.connection.detail,
          inputTranscript: snap.inputTranscript,
          outputTranscript: snap.outputTranscript,
          events: snap.events,
          memory: snap.memory,
          budget: snap.budget,
        }
      }
      case 'setState':     return { ...prev, state: args[0] as JarvisState }
      case 'setMode':      return { ...prev, mode: args[0] as JarvisMode }
      case 'setConnectionStatus': return {
        ...prev,
        connectionStatus: args[0] as ConnectionStatus,
        connectionDetail: args[1] as string ?? '',
      }
      case 'appendInput':  return { ...prev, inputTranscript: prev.inputTranscript + (args[0] as string) }
      case 'appendOutput': return { ...prev, outputTranscript: prev.outputTranscript + (args[0] as string) }
      case 'clearTranscripts': return { ...prev, inputTranscript: '', outputTranscript: '' }
      case 'logEvent': {
        const ev: LogEvent = { stamp: args[0] as string, level: args[1] as LogEvent['level'], message: args[2] as string }
        return { ...prev, events: [...prev.events.slice(-49), ev] }
      }
      case 'updateBudget':      return { ...prev, budget: args[0] as BudgetPayload }
      case 'updateMemoryStats': return { ...prev, memory: args[0] as MemoryStats }
      case 'showApproval':      return { ...prev, pendingApproval: args[0] as ApprovalPayload }
      case 'hideApproval':      return { ...prev, pendingApproval: null }
      case 'agentToolStart': {
        const ev = args[0] as AgentToolEvent
        return { ...prev, agentTools: [...prev.agentTools.slice(-29), { ...ev, status: 'running' }] }
      }
      case 'agentToolEnd': {
        const { id, status } = args[0] as { id: string; status: 'done' | 'error'; endedAt: number }
        return {
          ...prev,
          agentTools: prev.agentTools.map(t => t.id === id ? { ...t, ...args[0] as object, status } : t),
        }
      }
      case 'audioTelemetry': return { ...prev, audioTelemetry: args[0] as AudioTelemetry }
      case 'turnLatency': {
        const line = args[0] as string
        return { ...prev, latencyLines: [...prev.latencyLines.slice(-19), line] }
      }
      case 'setCameraActive': return { ...prev, cameraActive: args[0] as boolean }
      case 'cameraFrame':     return { ...prev, cameraFrame: args[0] as string }
      case 'cameraFocus':     return { ...prev, cameraFocus: args[0] as { box: unknown; label: string } }
      default: return prev
    }
  })
}

export function useJarvis() {
  const [ui, setUi] = useState<JarvisUIState>(INITIAL)
  const retryRef = useRef(0)
  const esRef = useRef<EventSource | null>(null)

  const connect = useCallback(() => {
    esRef.current?.close()
    const es = new EventSource('/events')
    esRef.current = es

    es.onopen = () => {
      retryRef.current = 0
      setUi(prev => ({ ...prev, connectionStatus: 'connected', connectionDetail: '' }))
    }

    es.onmessage = (e) => {
      try {
        const { command, args } = JSON.parse(e.data)
        applyCommand(setUi, command, args)
      } catch { /* ignore malformed */ }
    }

    es.onerror = () => {
      es.close()
      setUi(prev => ({ ...prev, connectionStatus: 'disconnected' }))
      const delay = Math.min(500 * 2 ** retryRef.current, 15000)
      retryRef.current++
      setTimeout(connect, delay)
    }
  }, [])

  useEffect(() => {
    connect()
    return () => esRef.current?.close()
  }, [connect])

  const sendCommand = useCallback(async (command: string, token: string) => {
    await fetch('/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Jarvis-Ui-Token': token },
      body: JSON.stringify({ command }),
    })
  }, [])

  const resolveApproval = useCallback(async (id: string, approved: boolean, token: string) => {
    await fetch('/approval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Jarvis-Ui-Token': token },
      body: JSON.stringify({ id, approved }),
    })
  }, [])

  return { ui, sendCommand, resolveApproval }
}
