import { useEffect, useRef, useState, useCallback } from 'react'
import type { Dispatch, SetStateAction } from 'react'
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

type UiDispatch = Dispatch<SetStateAction<JarvisUIState>>

function currentStamp() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function logEventFromArgs(args: unknown[]): LogEvent {
  const first = args[0]
  if (first && typeof first === 'object' && 'message' in first) {
    return first as LogEvent
  }
  return {
    stamp: currentStamp(),
    message: String(first ?? ''),
    level: (args[1] as LogEvent['level']) ?? 'info',
  }
}

function applyCommand(set: UiDispatch, command: string, args: unknown[]) {
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
          agentTools: snap.agentEvents ?? [],
          audioTelemetry: snap.audioTelemetry ?? null,
          latencyLines: snap.latency ?? [],
          cameraActive: Boolean(snap.cameraActive),
          cameraFrame: snap.cameraFrame ?? null,
          cameraFocus: snap.cameraFocus ?? null,
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
        const ev = logEventFromArgs(args)
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
        const ev = args[0] as AgentToolEvent
        let matched = false
        const updated = prev.agentTools.map(t => {
          if (t.id === ev.id) {
            matched = true
            return { ...t, ...ev }
          }
          return t
        })
        return {
          ...prev,
          agentTools: matched ? updated : [...prev.agentTools.slice(-29), ev],
        }
      }
      case 'audioTelemetry': return { ...prev, audioTelemetry: args[0] as AudioTelemetry }
      case 'turnLatency': {
        const line = args[0] as string
        return { ...prev, latencyLines: [...prev.latencyLines.slice(-19), line] }
      }
      case 'setCameraActive': {
        const active = args[0] as boolean
        return {
          ...prev,
          cameraActive: active,
          cameraFrame: active ? prev.cameraFrame : null,
          cameraFocus: active ? prev.cameraFocus : null,
        }
      }
      case 'cameraFrame':     return { ...prev, cameraActive: true, cameraFrame: args[0] as string }
      case 'cameraFocus':     return { ...prev, cameraFocus: args[0] as { box: unknown; label: string } }
      default: return prev
    }
  })
}

export function useJarvis() {
  const [ui, setUi] = useState<JarvisUIState>(INITIAL)
  const retryRef = useRef(0)
  const esRef = useRef<EventSource | null>(null)
  const connectRef = useRef<(() => void) | null>(null)

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
      setTimeout(() => connectRef.current?.(), delay)
    }
  }, [])

  useEffect(() => {
    connectRef.current = connect
    connect()
    return () => {
      connectRef.current = null
      esRef.current?.close()
    }
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
