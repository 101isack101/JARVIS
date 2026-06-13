import { motion, AnimatePresence } from 'framer-motion'
import type { AgentToolEvent, LogEvent } from '../types'

interface Props {
  agentTools: AgentToolEvent[]
  events: LogEvent[]
  latencyLines: string[]
}

const STATUS_ICON: Record<AgentToolEvent['status'], string> = {
  running: '⟳',
  done:    '✓',
  error:   '✗',
}

const STATUS_COLOR: Record<AgentToolEvent['status'], string> = {
  running: 'text-jarvis-yellow',
  done:    'text-jarvis-green',
  error:   'text-jarvis-red',
}

const LEVEL_COLOR: Record<string, string> = {
  ok:    'text-jarvis-green',
  warn:  'text-jarvis-yellow',
  error: 'text-jarvis-red',
  info:  'text-jarvis-text-dim',
}

export default function ThoughtLog({ agentTools, events, latencyLines }: Props) {
  const activeTools = agentTools.filter(t => t.status === 'running')
  const recentDone = agentTools.filter(t => t.status !== 'running').slice(-5)

  return (
    <div className="glass rounded-lg p-3 flex flex-col gap-3 text-[11px] font-mono">
      <span className="text-jarvis-text-faint text-[10px] uppercase tracking-widest">Thought Log</span>

      {/* Active tools */}
      <AnimatePresence>
        {activeTools.map(tool => (
          <motion.div
            key={tool.id}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 8 }}
            className="flex items-center gap-2 text-jarvis-yellow"
          >
            <motion.span
              animate={{ rotate: 360 }}
              transition={{ repeat: Infinity, duration: 1.2, ease: 'linear' }}
            >⟳</motion.span>
            <span className="truncate">{tool.name}</span>
          </motion.div>
        ))}
      </AnimatePresence>

      {/* Recent completed tools */}
      {recentDone.map(tool => (
        <div key={tool.id} className={`flex items-center gap-2 ${STATUS_COLOR[tool.status]} opacity-60`}>
          <span>{STATUS_ICON[tool.status]}</span>
          <span className="truncate">{tool.name}</span>
          {tool.endedAt && tool.startedAt && (
            <span className="ml-auto text-jarvis-text-faint shrink-0">
              {((tool.endedAt - tool.startedAt) / 1000).toFixed(1)}s
            </span>
          )}
        </div>
      ))}

      {/* Divider */}
      {events.length > 0 && <div className="border-t border-jarvis-border" />}

      {/* Log events */}
      <div className="flex flex-col gap-1 max-h-28 overflow-y-auto">
        {events.slice(-8).map((ev, i) => (
          <div key={i} className="flex gap-2 leading-tight">
            <span className="text-jarvis-text-faint shrink-0">{ev.stamp}</span>
            <span className={`truncate ${LEVEL_COLOR[ev.level] ?? 'text-jarvis-text-dim'}`}>{ev.message}</span>
          </div>
        ))}
      </div>

      {/* Latency */}
      {latencyLines.length > 0 && (
        <>
          <div className="border-t border-jarvis-border" />
          <div className="flex flex-col gap-0.5 max-h-16 overflow-y-auto text-jarvis-text-faint">
            {latencyLines.slice(-4).map((l, i) => <span key={i}>{l}</span>)}
          </div>
        </>
      )}
    </div>
  )
}
