import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, RefObject } from 'react'
import {
  Bot, Camera, CirclePower, Cloud, Clock3, Download, Gauge, Keyboard, MemoryStick,
  Mic, Power, RefreshCcw, Settings, Thermometer, Trash2, Wifi, X,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './index.css'
import { useJarvis } from './useJarvis'
import ApprovalModal from './components/ApprovalModal'
import type {
  AgentToolEvent, AudioTelemetry, BudgetPayload, JarvisMode, JarvisState, LogEvent,
  ConversationMessage, RuntimeTelemetry, SystemStats as SystemStatsData, Weather,
  MissionSummary,
} from './types'

const STATE_LABEL: Record<JarvisState, string> = {
  idle: 'Listening for wake word...',
  listening: 'Listening...',
  thinking: 'Processing request...',
  speaking: 'Responding...',
  blocked: 'Budget blocked',
}

const STATE_LOAD: Record<JarvisState, number> = {
  idle: 8,
  listening: 22,
  thinking: 68,
  speaking: 38,
  blocked: 96,
}

function useClock() {
  const [now, setNow] = useState(() => new Date())
  const [startedAt] = useState(() => Date.now())
  const [uptimeMs, setUptimeMs] = useState(0)

  useEffect(() => {
    const iv = setInterval(() => {
      setNow(new Date())
      setUptimeMs(Date.now() - startedAt)
    }, 1000)
    return () => clearInterval(iv)
  }, [startedAt])

  return { now, uptimeMs }
}

function formatTime(date: Date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function formatDate(date: Date) {
  return date.toLocaleDateString([], { month: 'long', day: 'numeric', year: 'numeric' })
}

function formatDuration(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(total / 3600).toString().padStart(2, '0')
  const m = Math.floor((total % 3600) / 60).toString().padStart(2, '0')
  const s = Math.floor(total % 60).toString().padStart(2, '0')
  return `${h}:${m}:${s}`
}

function fileStamp(date = new Date()) {
  const yyyy = date.getFullYear()
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  const hh = String(date.getHours()).padStart(2, '0')
  const mi = String(date.getMinutes()).padStart(2, '0')
  const ss = String(date.getSeconds()).padStart(2, '0')
  return `${yyyy}${mm}${dd}-${hh}${mi}${ss}`
}

function buildConversationMarkdown(
  messages: ConversationMessage[],
  inputTranscript: string,
  outputTranscript: string,
) {
  const fallbackCount = inputTranscript.trim() || outputTranscript.trim() ? 2 : 0
  const lines = [
    '# Conversacion JARVIS',
    '',
    `Extraida: ${new Date().toLocaleString()}`,
    `Mensajes: ${messages.length || fallbackCount}`,
    '',
  ]

  if (messages.length) {
    for (const message of messages) {
      const who = message.role === 'user' ? 'Isaac' : 'JARVIS'
      lines.push(`## ${who} - ${message.stamp || 'sin hora'}`, '', message.text.trim(), '')
    }
  } else {
    if (inputTranscript.trim()) lines.push('## Isaac', '', inputTranscript.trim(), '')
    if (outputTranscript.trim()) lines.push('## JARVIS', '', outputTranscript.trim(), '')
  }

  return lines.join('\n').trim() + '\n'
}

function downloadTextFile(filename: string, text: string) {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function Panel({ title, icon: Icon, action, children }: {
  title: string
  icon: typeof Bot
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="hud-panel">
      <header className="hud-panel-header">
        <span className="hud-panel-title">
          <Icon size={15} />
          {title}
        </span>
        {action}
      </header>
      <div className="hud-panel-body">{children}</div>
    </section>
  )
}

function MiniButton({ title, children, onClick }: {
  title: string
  children: React.ReactNode
  onClick?: () => void
}) {
  return (
    <button className="hud-icon-button" type="button" title={title} aria-label={title} onClick={onClick}>
      {children}
    </button>
  )
}

function ProgressLine({ label, value, suffix = '%' }: { label: string; value: number; suffix?: string }) {
  const pct = Math.max(0, Math.min(value, 100))
  return (
    <div className="hud-progress-row">
      <div className="hud-progress-label">
        <span>{label}</span>
        <span>{Math.round(value)}{suffix}</span>
      </div>
      <div className="hud-progress-track">
        <div className="hud-progress-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function money(value: number, digits = 4) {
  return `$${value.toFixed(digits)}`
}

function rate(value: number, digits = 4) {
  return `${money(value, digits)}/min`
}

function RefreshButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="hud-refresh-button" type="button" title="Refresh" aria-label="Refresh" onClick={onClick}>
      <RefreshCcw size={14} />
    </button>
  )
}

function SystemStats({ stats, state, events, tools, onRefresh }: {
  stats: SystemStatsData | null
  state: JarvisState
  events: LogEvent[]
  tools: AgentToolEvent[]
  onRefresh: () => void
}) {
  // Datos reales (psutil) cuando llegan; si la sesion aun no emite, caemos en
  // una estimacion derivada del estado para no mostrar el panel vacio.
  const cpu = stats ? stats.cpu : STATE_LOAD[state]
  const ram = stats
    ? stats.ram
    : Math.min(92, 34 + tools.filter(t => t.status === 'running').length * 14 + events.length)
  const diskLabel = stats ? `${stats.diskUsedGb}/${stats.diskTotalGb} GB` : '—'

  return (
    <Panel title="System Stats" icon={MemoryStick} action={<RefreshButton onClick={onRefresh} />}>
      <ProgressLine label="CPU Usage" value={cpu} />
      <ProgressLine label="RAM Usage" value={ram} />
      <div className="hud-stat-grid">
        <div><span>CPU</span><strong>{cpu}%</strong></div>
        <div><span>Memory</span><strong>{ram}%</strong></div>
        <div><span>Disk</span><strong>{diskLabel}</strong></div>
      </div>
    </Panel>
  )
}

const TERMINAL_MISSION = new Set(['completed', 'cancelled', 'blocked', 'failed'])

function MissionPanel({ missions, onCancel }: {
  missions: MissionSummary[]
  onCancel: (missionId: string) => void
}) {
  const recent = [...missions].reverse().slice(0, 3)
  return (
    <Panel title="Missions" icon={Bot} action={<span className="hud-mission-count">{missions.length}</span>}>
      {!recent.length && <div className="hud-mission-empty">Sin misiones activas</div>}
      <div className="hud-mission-list">
        {recent.map(mission => {
          const total = Math.max(0, mission.total_steps)
          const progress = total ? Math.round(mission.completed_steps * 100 / total) : 0
          const cancellable = !TERMINAL_MISSION.has(mission.status)
          return (
            <article className={`hud-mission hud-mission-${mission.status}`} key={mission.id}>
              <div className="hud-mission-topline">
                <span>{mission.status.replace('_', ' ')}</span>
                <b>{mission.completed_steps}/{total || 'â€”'}</b>
                {cancellable && (
                  <button type="button" title="Cancelar misión" onClick={() => onCancel(mission.id)}>
                    <X size={12} />
                  </button>
                )}
              </div>
              <p title={mission.goal}>{mission.goal}</p>
              <div className="hud-mission-track"><i style={{ width: `${progress}%` }} /></div>
              {mission.error_code && <small>{mission.error_code}</small>}
            </article>
          )
        })}
      </div>
    </Panel>
  )
}

function TelemetryPanel({ telemetry, budget }: {
  telemetry: RuntimeTelemetry | null
  budget: BudgetPayload | null
}) {
  const geminiTokens = telemetry?.gemini.tokensLabel ?? budget?.gemini.tokensLabel ?? '0'
  const claudeTokens = telemetry?.claude.tokensLabel ?? budget?.claude.tokensLabel ?? '0'
  const totalUsd = telemetry?.totalUsd ?? budget?.totalUsd ?? 0
  const totalTokens = telemetry?.totalTokensLabel ?? '0'
  const camera = telemetry?.camera

  return (
    <Panel
      title="Telemetría"
      icon={Gauge}
      action={<span className="hud-clock-mini">{telemetry?.stamp ?? '—'}</span>}
    >
      <div className="hud-telemetry-summary">
        <div>
          <span>Session cost</span>
          <strong>{money(totalUsd)}</strong>
          <small>{rate(telemetry?.totalUsdPerMin ?? 0)}</small>
        </div>
        <div>
          <span>Tokens</span>
          <strong>{totalTokens}</strong>
          <small>{Math.round(telemetry?.totalTokensPerMin ?? 0)}/min</small>
        </div>
      </div>

      <div className="hud-telemetry-rows">
        <div className="hud-telemetry-row">
          <span>Gemini</span>
          <strong>{geminiTokens}</strong>
          <b>{money(telemetry?.gemini.costUsd ?? budget?.gemini.spentUsd ?? 0, 5)}</b>
          <em>{rate(telemetry?.gemini.usdPerMin ?? 0, 5)}</em>
        </div>
        <div className="hud-telemetry-row">
          <span>Claude</span>
          <strong>{claudeTokens}</strong>
          <b>{money(telemetry?.claude.costUsd ?? budget?.claude.spentUsd ?? 0, 5)}</b>
          <em>{rate(telemetry?.claude.usdPerMin ?? 0, 5)}</em>
        </div>
      </div>

      <div className="hud-telemetry-camera">
        <div>
          <span>Vision stream</span>
          <strong>{(camera?.streamFps ?? 0).toFixed(1)} FPS</strong>
          <small>{(camera?.streamKbps ?? 0).toFixed(1)} KB/s</small>
        </div>
        <div>
          <span>Preview</span>
          <strong>{(camera?.previewFps ?? 0).toFixed(1)} FPS</strong>
          <small>{(camera?.previewKbps ?? 0).toFixed(1)} KB/s</small>
        </div>
        <div>
          <span>Data</span>
          <strong>{(camera?.streamMb ?? 0).toFixed(2)} MB</strong>
          <small>{(camera?.avgFrameKb ?? 0).toFixed(1)} KB/frame</small>
        </div>
      </div>
    </Panel>
  )
}

function WeatherPanel({ weather, onRefresh }: { weather: Weather | null; onRefresh: () => void }) {
  return (
    <Panel title="Weather" icon={Cloud} action={<RefreshButton onClick={onRefresh} />}>
      <div className="hud-weather">
        <div>
          <div className="hud-temp">{weather ? `${weather.tempC}°C` : '—'}</div>
          <div className="hud-place">{weather ? weather.place : 'Locating…'}</div>
          <div className="hud-muted">{weather ? weather.desc : 'fetching weather…'}</div>
        </div>
        <Cloud className="hud-weather-icon" size={44} />
      </div>
      <div className="hud-stat-grid">
        <div><span>Humidity</span><strong>{weather ? `${weather.humidity}%` : '—'}</strong></div>
        <div><span>Wind</span><strong>{weather ? `${weather.windMs} m/s` : '—'}</strong></div>
        <div><span>Feels Like</span><strong>{weather ? `${weather.feelsC}°C` : '—'}</strong></div>
      </div>
    </Panel>
  )
}

function CameraWidget({ active, frame, focus }: {
  active: boolean
  frame: string | null
  focus: { box: unknown; label: string } | null
}) {
  return (
    <Panel
      title="Camera"
      icon={Camera}
      action={
        <div className="hud-panel-actions">
          <Camera size={14} />
          <CirclePower size={14} className={active ? 'hud-action-on' : ''} />
        </div>
      }
    >
      <div className="hud-camera-frame">
        {frame ? (
          <>
            <img src={`data:image/jpeg;base64,${frame}`} alt="Camera preview" />
            {focus?.label && <span className="hud-camera-focus">{focus.label}</span>}
          </>
        ) : (
          <div className="hud-camera-off">
            <Camera size={32} />
            <span>Camera Off</span>
          </div>
        )}
      </div>
      <div className="hud-camera-caption">
        {active ? 'Camera feed active.' : 'Camera is inactive.'}
      </div>
    </Panel>
  )
}

function UptimePanel({ uptimeMs, commands }: { uptimeMs: number; commands: number }) {
  const uptime = formatDuration(uptimeMs)
  return (
    <Panel title="System Uptime" icon={Clock3} action={<span className="hud-clock-mini">{uptime}</span>}>
      <div className="hud-uptime-main">{uptime}</div>
      <div className="hud-stat-grid hud-two">
        <div><span>Session</span><strong>1</strong></div>
        <div><span>Commands</span><strong>{commands}</strong></div>
      </div>
      <ProgressLine label="System Load" value={26} suffix="%" />
      <div className="hud-load-label">Moderate</div>
    </Panel>
  )
}

const PARTICLES = Array.from({ length: 30 }, (_, i) => ({
  angle: (i * 137.5) % 360,
  radius: 74 + (i % 7) * 13,
  size: 2 + (i % 4),
  delay: i * 0.11,
  duration: 2.9 + (i % 6) * 0.37,
}))

function CoreOrb({ state, audioTelemetry }: { state: JarvisState; audioTelemetry: AudioTelemetry | null }) {
  const voiceEnergy =
    state === 'speaking' ? 1 :
    state === 'thinking' ? 0.72 :
    state === 'listening' ? Math.max(0.45, Math.min(1, audioTelemetry?.wakewordPeak ?? 0.5)) :
    state === 'blocked' ? 0.28 :
    0.36
  const coreStyle = { '--voice-energy': voiceEnergy } as CSSProperties

  return (
    <main className="hud-center">
      <div className={`hud-orb hud-orb-${state}`} style={coreStyle}>
        <div className="hud-particle-field" aria-hidden="true">
          {PARTICLES.map((particle, i) => (
            <i
              key={i}
              style={{
                '--particle-angle': `${particle.angle}deg`,
                '--particle-radius': `${particle.radius}px`,
                '--particle-size': `${particle.size}px`,
                '--particle-delay': `${particle.delay}s`,
                '--particle-duration': `${particle.duration}s`,
              } as CSSProperties}
            />
          ))}
        </div>
        <div className="hud-energy-veil" aria-hidden="true" />
        <div className="hud-ring hud-ring-a" />
        <div className="hud-ring hud-ring-b" />
        <div className="hud-ring hud-ring-c" />
        <div className="hud-ring hud-ring-d" />
        <div className="hud-core" aria-label={`Jarvis core ${state}`}>
          <div className="hud-core-segments" aria-hidden="true" />
          <div className="hud-core-mesh" aria-hidden="true" />
          <div className="hud-core-highlight" aria-hidden="true" />
        </div>
      </div>
      <h1>J.A.R.V.I.S</h1>
      <div className="hud-status-pill">
        <span className="hud-live-dot" />
        {STATE_LABEL[state]}
      </div>
    </main>
  )
}

function Conversation({ messages, onClear, onExtract, onSend, inputRef, canSend, extractLabel }: {
  messages: ConversationMessage[]
  onClear: () => void
  onExtract: () => void
  onSend: (text: string) => void
  inputRef: RefObject<HTMLInputElement | null>
  canSend: boolean
  extractLabel: string
}) {
  const hasContent = messages.length > 0
  const [draft, setDraft] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  const submit = () => {
    const text = draft.trim()
    if (!text || !canSend) return
    onSend(text)
    setDraft('')
  }

  return (
    <aside className="hud-conversation">
      <header className="hud-conversation-header">
        <span>Conversación</span>
        <div>
          <button type="button" onClick={onClear}><Trash2 size={13} />Limpiar</button>
          <button type="button" onClick={onExtract}><Download size={13} />{extractLabel}</button>
        </div>
      </header>
      <div className="hud-conversation-feed">
        {hasContent ? (
          <>
            {messages.map((message) => {
              const isUser = message.role === 'user'
              return (
                <div
                  key={message.id}
                  className={`hud-message-row ${isUser ? 'hud-message-row-user' : 'hud-message-row-jarvis'}`}
                >
                  <article
                    className={`hud-message ${isUser ? 'hud-message-user' : 'hud-message-jarvis'}`}
                    aria-label={isUser ? 'Ventana de texto de Isaac' : 'Ventana de texto de JARVIS'}
                  >
                    <header className="hud-message-meta">
                      <span className="hud-message-title">{isUser ? 'Isaac' : 'JARVIS'}</span>
                      <small>{isUser ? 'Ventana de texto' : 'Ventana de respuesta'} | {message.stamp}</small>
                    </header>
                    {isUser ? (
                      <div className="hud-message-body">
                        <p>{message.text}</p>
                      </div>
                    ) : (
                      <div className="hud-message-body hud-message-markdown">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
                      </div>
                    )}
                  </article>
                </div>
              )
            })}
          </>
        ) : (
          <div className="hud-message-row hud-message-row-jarvis">
            <article className="hud-message hud-message-jarvis" aria-label="Ventana de texto de JARVIS">
              <header className="hud-message-meta">
                <span className="hud-message-title">JARVIS</span>
                <small>Ventana de respuesta</small>
              </header>
              <div className="hud-message-body">
                <p>Hello, I am JARVIS. How can I assist you today, sir?</p>
              </div>
            </article>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <form
        className="hud-input-row"
        onSubmit={(e) => { e.preventDefault(); submit() }}
      >
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={canSend ? 'Type a message...' : 'Connecting to JARVIS...'}
          aria-label="Message"
          disabled={!canSend}
        />
        <button type="submit" title="Send" aria-label="Send" disabled={!canSend || !draft.trim()}>
          Send
        </button>
      </form>
    </aside>
  )
}

function SettingsMenu({ version, online, mode, connectionDetail, onClose, onShutdown }: {
  version: string
  online: boolean
  mode: JarvisMode
  connectionDetail: string
  onClose: () => void
  onShutdown: () => void
}) {
  return (
    <>
      <div className="hud-settings-backdrop" onClick={onClose} />
      <div className="hud-settings-menu" role="dialog" aria-label="Settings">
        <header>
          <span>Settings</span>
          <button type="button" title="Close" aria-label="Close" onClick={onClose}><X size={14} /></button>
        </header>
        <dl className="hud-settings-grid">
          <dt>Version</dt><dd>{version || '—'}</dd>
          <dt>Connection</dt><dd>{online ? 'Online' : (connectionDetail || 'Offline')}</dd>
          <dt>Listen mode</dt><dd>{mode}</dd>
        </dl>
        <button type="button" className="hud-settings-danger" onClick={onShutdown}>
          <Power size={14} /> Shutdown JARVIS
        </button>
      </div>
    </>
  )
}

function TopBar({ version, online, now, mode, connectionDetail, weather, settingsOpen, onToggleSettings, onShutdown }: {
  version: string
  online: boolean
  now: Date
  mode: JarvisMode
  connectionDetail: string
  weather: Weather | null
  settingsOpen: boolean
  onToggleSettings: () => void
  onShutdown: () => void
}) {
  return (
    <header className="hud-topbar">
      <div className="hud-brand">
        <span>J.A.R.V.I.S</span>
        <b className={online ? 'online' : 'offline'}>
          <Wifi size={12} />
          {online ? 'Online' : 'Offline'}
        </b>
      </div>
      <div className="hud-top-chip">
        <Clock3 size={14} />
        <span>{formatTime(now)}</span>
        <i />
        <span>{formatDate(now)}</span>
      </div>
      <div className="hud-top-right">
        <div className="hud-top-chip">
          <Thermometer size={14} />
          <span>{weather ? `${weather.tempC}°C` : '—'}</span>
          <small>{weather ? weather.place.split(',')[0] : '…'}</small>
        </div>
        <div className="hud-settings-anchor">
          <button
            className={`hud-icon-button${settingsOpen ? ' hud-icon-button-on' : ''}`}
            type="button" title="Settings" aria-label="Settings"
            aria-expanded={settingsOpen} onClick={onToggleSettings}
          >
            <Settings size={18} />
          </button>
          {settingsOpen && (
            <SettingsMenu
              version={version} online={online} mode={mode} connectionDetail={connectionDetail}
              onClose={onToggleSettings} onShutdown={onShutdown}
            />
          )}
        </div>
      </div>
      <span className="hud-version">{version}</span>
    </header>
  )
}

function BottomControls({ onCamera, onMic, onKeyboard, cameraActive, mode }: {
  onCamera: () => void
  onMic: () => void
  onKeyboard: () => void
  cameraActive: boolean
  mode: JarvisMode
}) {
  const micTitle = mode === 'LIBRE' ? 'Listening mode: LIBRE (click for PTT)' : 'Push-to-talk (click for LIBRE)'
  return (
    <div className="hud-bottom-controls">
      <button
        className={`hud-icon-button${cameraActive ? ' hud-icon-button-on' : ''}`}
        type="button" title="Toggle camera" aria-label="Toggle camera"
        aria-pressed={cameraActive} onClick={onCamera}
      >
        <Camera size={22} />
      </button>
      <div className="hud-mic-stack">
        <button
          className={`hud-icon-button${mode === 'LIBRE' ? ' hud-icon-button-on' : ''}`}
          type="button" title={micTitle} aria-label={micTitle}
          aria-pressed={mode === 'LIBRE'} onClick={onMic}
        >
          <Mic size={22} />
        </button>
        <div className={`hud-dots${mode === 'LIBRE' ? ' hud-dots-live' : ''}`}>
          <span /><span /><span /><span />
        </div>
      </div>
      <MiniButton title="Focus message box" onClick={onKeyboard}><Keyboard size={22} /></MiniButton>
    </div>
  )
}

export default function App() {
  const { ui, sendCommand, resolveApproval } = useJarvis()
  const { now, uptimeMs } = useClock()
  const inputRef = useRef<HTMLInputElement>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [extractLabel, setExtractLabel] = useState('Extraer conversacion')
  const connected = ui.connectionStatus === 'connected'
  const commands = useMemo(
    () => ui.agentTools.filter(t => t.status !== 'running').length,
    [ui.agentTools],
  )

  // Todo comando hacia el backend requiere el token de UI del snapshot. Si aun
  // no llego (sesion arrancando), el control queda inerte en vez de fallar.
  const dispatch = (command: string, args: Record<string, unknown> = {}) => {
    if (ui.uiToken) void sendCommand(command, ui.uiToken, args)
  }

  const approve = () => {
    if (ui.pendingApproval) resolveApproval(ui.pendingApproval.id, true, ui.uiToken)
  }

  const reject = () => {
    if (ui.pendingApproval) resolveApproval(ui.pendingApproval.id, false, ui.uiToken)
  }

  const clearConversation = () => dispatch('clearTranscripts')

  const extractConversation = async () => {
    const text = buildConversationMarkdown(
      ui.conversationMessages,
      ui.inputTranscript,
      ui.outputTranscript,
    )
    const hasContent = ui.conversationMessages.length > 0
      || ui.inputTranscript.trim().length > 0
      || ui.outputTranscript.trim().length > 0
    if (!hasContent) {
      setExtractLabel('Sin contenido')
      window.setTimeout(() => setExtractLabel('Extraer conversacion'), 1800)
      return
    }

    try {
      if (ui.uiToken) {
        try {
          await sendCommand('extractConversation', ui.uiToken)
        } catch {
          // La descarga local sigue funcionando aunque el bridge no responda.
        }
      }
      downloadTextFile(`jarvis-conversation-${fileStamp()}.md`, text)
      try {
        await navigator.clipboard?.writeText(text)
      } catch {
        // La descarga es la extraccion principal; clipboard es solo extra.
      }
      setExtractLabel('Descargada')
    } catch {
      setExtractLabel('Error')
    } finally {
      window.setTimeout(() => setExtractLabel('Extraer conversacion'), 2200)
    }
  }

  const sendText = (text: string) => dispatch('sendText', { text })
  const toggleMic = () => dispatch('toggleMode')
  const toggleCamera = () => dispatch('toggleCamera')
  const refreshStats = () => dispatch('refreshStats')
  const refreshWeather = () => dispatch('refreshWeather')
  const cancelMission = (missionId: string) => dispatch('cancelMission', { missionId })
  const shutdown = () => { dispatch('close'); setSettingsOpen(false) }
  const focusInput = () => inputRef.current?.focus()

  return (
    <div className="hud-shell">
      <TopBar
        version={ui.version || 'v1.03'}
        online={connected}
        now={now}
        mode={ui.mode}
        connectionDetail={ui.connectionDetail}
        weather={ui.weather}
        settingsOpen={settingsOpen}
        onToggleSettings={() => setSettingsOpen(o => !o)}
        onShutdown={shutdown}
      />
      <div className="hud-layout">
        <aside className="hud-left">
          <SystemStats
            stats={ui.systemStats}
            state={ui.state}
            events={ui.events}
            tools={ui.agentTools}
            onRefresh={refreshStats}
          />
          <MissionPanel missions={ui.missions} onCancel={cancelMission} />
          <TelemetryPanel telemetry={ui.runtimeTelemetry} budget={ui.budget} />
          <WeatherPanel weather={ui.weather} onRefresh={refreshWeather} />
          <CameraWidget active={ui.cameraActive} frame={ui.cameraFrame} focus={ui.cameraFocus} />
          <UptimePanel uptimeMs={uptimeMs} commands={commands} />
        </aside>
        <CoreOrb state={ui.state} audioTelemetry={ui.audioTelemetry} />
        <Conversation
          messages={ui.conversationMessages}
          onClear={clearConversation}
          onExtract={extractConversation}
          extractLabel={extractLabel}
          onSend={sendText}
          inputRef={inputRef}
          canSend={connected}
        />
      </div>
      <BottomControls
        onCamera={toggleCamera}
        onMic={toggleMic}
        onKeyboard={focusInput}
        cameraActive={ui.cameraActive}
        mode={ui.mode}
      />
      <ApprovalModal approval={ui.pendingApproval} onApprove={approve} onReject={reject} />
    </div>
  )
}
