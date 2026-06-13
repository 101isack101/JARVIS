import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import {
  Bot, Camera, CirclePower, Cloud, Clock3, Download, Keyboard, MemoryStick,
  Mic, RefreshCcw, Settings, Thermometer, Trash2, Wifi,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './index.css'
import { useJarvis } from './useJarvis'
import ApprovalModal from './components/ApprovalModal'
import type { AgentToolEvent, AudioTelemetry, JarvisState, LogEvent } from './types'

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

function SystemStats({ state, events, tools }: {
  state: JarvisState
  events: LogEvent[]
  tools: AgentToolEvent[]
}) {
  const cpu = STATE_LOAD[state]
  const memory = Math.min(92, 34 + tools.filter(t => t.status === 'running').length * 14 + events.length)
  const disk = 43

  return (
    <Panel title="System Stats" icon={MemoryStick} action={<RefreshCcw size={14} />}>
      <ProgressLine label="CPU Usage" value={cpu} />
      <ProgressLine label="RAM Usage" value={memory} />
      <div className="hud-stat-grid">
        <div><span>CPU</span><strong>{cpu}%</strong></div>
        <div><span>Memory</span><strong>{memory}%</strong></div>
        <div><span>Disk</span><strong>{disk}/475 GB</strong></div>
      </div>
    </Panel>
  )
}

function WeatherPanel() {
  return (
    <Panel title="Weather" icon={Cloud} action={<RefreshCcw size={14} />}>
      <div className="hud-weather">
        <div>
          <div className="hud-temp">25.2°C</div>
          <div className="hud-place">Quezon City, PH</div>
          <div className="hud-muted">overcast clouds</div>
        </div>
        <Cloud className="hud-weather-icon" size={44} />
      </div>
      <div className="hud-stat-grid">
        <div><span>Humidity</span><strong>94%</strong></div>
        <div><span>Wind</span><strong>5.8 m/s</strong></div>
        <div><span>Feels Like</span><strong>26.3°C</strong></div>
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

function Conversation({ input, output, onClear, onExtract }: {
  input: string
  output: string
  onClear: () => void
  onExtract: () => void
}) {
  const hasContent = input || output

  return (
    <aside className="hud-conversation">
      <header className="hud-conversation-header">
        <span>Conversation</span>
        <div>
          <button type="button" onClick={onClear}><Trash2 size={13} />Clear</button>
          <button type="button" onClick={onExtract}><Download size={13} />Extract Conversation</button>
        </div>
      </header>
      <div className="hud-conversation-feed">
        {hasContent ? (
          <>
            {input && (
              <div className="hud-message hud-message-user">
                <span>Isaac</span>
                <p>{input}</p>
              </div>
            )}
            {output && (
              <div className="hud-message hud-message-jarvis">
                <span>JARVIS</span>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{output}</ReactMarkdown>
              </div>
            )}
          </>
        ) : (
          <div className="hud-message hud-message-jarvis">
            <span>JARVIS</span>
            <p>Hello, I am JARVIS. How can I assist you today, sir?</p>
          </div>
        )}
      </div>
      <div className="hud-input-row">
        <input placeholder="Type a message..." aria-label="Message" />
        <button type="button" title="Send" aria-label="Send">Send</button>
      </div>
    </aside>
  )
}

function TopBar({ version, online, now }: { version: string; online: boolean; now: Date }) {
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
          <span>25.2°C</span>
          <small>Quezon City</small>
        </div>
        <MiniButton title="Settings"><Settings size={18} /></MiniButton>
      </div>
      <span className="hud-version">{version}</span>
    </header>
  )
}

function BottomControls({ onCamera, onMic }: { onCamera: () => void; onMic: () => void }) {
  return (
    <div className="hud-bottom-controls">
      <MiniButton title="Camera" onClick={onCamera}><Camera size={22} /></MiniButton>
      <div className="hud-mic-stack">
        <MiniButton title="Microphone" onClick={onMic}><Mic size={22} /></MiniButton>
        <div className="hud-dots"><span /><span /><span /><span /></div>
      </div>
      <MiniButton title="Keyboard"><Keyboard size={22} /></MiniButton>
    </div>
  )
}

export default function App() {
  const { ui, sendCommand, resolveApproval } = useJarvis()
  const { now, uptimeMs } = useClock()
  const commands = useMemo(
    () => ui.agentTools.filter(t => t.status !== 'running').length,
    [ui.agentTools],
  )

  const approve = () => {
    if (ui.pendingApproval) resolveApproval(ui.pendingApproval.id, true, ui.uiToken)
  }

  const reject = () => {
    if (ui.pendingApproval) resolveApproval(ui.pendingApproval.id, false, ui.uiToken)
  }

  const clearConversation = () => {
    if (ui.uiToken) sendCommand('clearTranscripts', ui.uiToken)
  }

  const extractConversation = () => {
    const text = [`Isaac:\n${ui.inputTranscript}`, `JARVIS:\n${ui.outputTranscript}`].join('\n\n')
    void navigator.clipboard?.writeText(text)
  }

  const triggerCamera = () => {
    if (ui.uiToken) sendCommand('openDashboard', ui.uiToken)
  }

  return (
    <div className="hud-shell">
      <TopBar version={ui.version || 'v1.02'} online={ui.connectionStatus === 'connected'} now={now} />
      <div className="hud-layout">
        <aside className="hud-left">
          <SystemStats state={ui.state} events={ui.events} tools={ui.agentTools} />
          <WeatherPanel />
          <CameraWidget active={ui.cameraActive} frame={ui.cameraFrame} focus={ui.cameraFocus} />
          <UptimePanel uptimeMs={uptimeMs} commands={commands} />
        </aside>
        <CoreOrb state={ui.state} audioTelemetry={ui.audioTelemetry} />
        <Conversation
          input={ui.inputTranscript}
          output={ui.outputTranscript}
          onClear={clearConversation}
          onExtract={extractConversation}
        />
      </div>
      <BottomControls onCamera={triggerCamera} onMic={() => undefined} />
      <ApprovalModal approval={ui.pendingApproval} onApprove={approve} onReject={reject} />
    </div>
  )
}
