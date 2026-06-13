import { motion } from 'framer-motion'
import type { JarvisState, JarvisMode, ConnectionStatus } from '../types'

interface Props {
  version: string
  state: JarvisState
  mode: JarvisMode
  connectionStatus: ConnectionStatus
}

const STATE_LABELS: Record<JarvisState, string> = {
  idle: 'IDLE',
  listening: 'LISTENING',
  thinking: 'THINKING',
  speaking: 'SPEAKING',
  blocked: 'BLOCKED',
}

const STATE_COLORS: Record<JarvisState, string> = {
  idle:      'text-jarvis-text-dim',
  listening: 'text-jarvis-cyan',
  thinking:  'text-jarvis-accent',
  speaking:  'text-jarvis-green',
  blocked:   'text-jarvis-red',
}

const CONN_COLORS: Record<ConnectionStatus, string> = {
  connecting:   'bg-jarvis-yellow',
  connected:    'bg-jarvis-green',
  disconnected: 'bg-jarvis-red',
}

export default function StatusBar({ version, state, mode, connectionStatus }: Props) {
  return (
    <div className="flex items-center justify-between px-4 py-2 border-b border-jarvis-border glass">
      {/* Left: version */}
      <span className="text-jarvis-text-dim text-xs font-mono tracking-widest uppercase">
        JARVIS {version}
      </span>

      {/* Center: state */}
      <motion.span
        key={state}
        initial={{ opacity: 0, y: -4 }}
        animate={{ opacity: 1, y: 0 }}
        className={`text-xs font-mono font-semibold tracking-[0.2em] ${STATE_COLORS[state]}`}
      >
        {STATE_LABELS[state]}
      </motion.span>

      {/* Right: mode + connection dot */}
      <div className="flex items-center gap-3">
        <span className="text-jarvis-text-faint text-xs font-mono">{mode}</span>
        <motion.span
          animate={{ opacity: connectionStatus === 'connecting' ? [1, 0.3, 1] : 1 }}
          transition={{ repeat: Infinity, duration: 1.2, ease: 'easeInOut' }}
          className={`w-2 h-2 rounded-full ${CONN_COLORS[connectionStatus]}`}
        />
      </div>
    </div>
  )
}
