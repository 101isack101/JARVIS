import { motion, AnimatePresence } from 'framer-motion'
import type { AgentToolEvent, LogEvent } from '../types'

interface Props {
  agentTools: AgentToolEvent[]
  events: LogEvent[]
  latencyLines: string[]
}

const STATUS_ICON: Record<AgentToolEvent['status'], string> = {
  running: '...',
  ok: 'OK',
  error: 'ERR',
}

const STATUS_COLOR: Record<AgentToolEvent['status'], string> = {
  running: 'text-jarvis-yellow',
  ok: 'text-jarvis-green',
  error: 'text-jarvis-red',
}

const LEVEL_COLOR: Record<string, string> = {
  ok: 'text-jarvis-green',
  warn: 'text-jarvis-yellow',
  error: 'text-jarvis-red',
  info: 'text-jarvis-text-dim',
}

function toolKey(tool: AgentToolEvent, index: number) {
  return tool.id || `${tool.name}-${tool.stamp}-${index}`
}

export default function ThoughtLog({ agentTools, events, latencyLines }: Props) {
  const activeTools = agentTools.filter(t => t.status === 'running')
  const recentDone = agentTools.filter(t => t.status !== 'running').slice(-5)

  return (
    <div className="glass rounded-lg p-3 flex flex-col gap-3 text-[11px] font-mono">
      <span className="text-jarvis-text-faint text-[10px] uppercase tracking-widest">Thought Log</span>

      <AnimatePresence>
        {activeTools.map((tool, i) => (
          <motion.div
            key={toolKey(tool, i)}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 8 }}
            className="flex items-center gap-2 text-jarvis-yellow min-w-0"
          >
            <motion.span
              animate={{ opacity: [0.35, 1, 0.35] }}
              transition={{ repeat: Infinity, duration: 1.2, ease: 'linear' }}
              className="w-6 shrink-0"
            >
              ...
            </motion.span>
            <span className="truncate">{tool.name}</span>
            {tool.summary && <span className="truncate text-jarvis-text-faint">{tool.summary}</span>}
          </motion.div>
        ))}
      </AnimatePresence>

      {recentDone.map((tool, i) => (
        <div
          key={toolKey(tool, i)}
          className={`flex items-center gap-2 min-w-0 ${STATUS_COLOR[tool.status]} opacity-60`}
        >
          <span className="w-6 shrink-0">{STATUS_ICON[tool.status]}</span>
          <span className="truncate">{tool.name}</span>
          {typeof tool.elapsedMs === 'number' && (
            <span className="ml-auto text-jarvis-text-faint shrink-0">
              {(tool.elapsedMs / 1000).toFixed(1)}s
            </span>
          )}
        </div>
      ))}

      {events.length > 0 && <div className="border-t border-jarvis-border" />}

      <div className="flex flex-col gap-1 max-h-28 overflow-y-auto">
        {events.slice(-8).map((ev, i) => (
          <div key={`${ev.stamp}-${i}`} className="flex gap-2 leading-tight min-w-0">
            <span className="text-jarvis-text-faint shrink-0">{ev.stamp}</span>
            <span className={`truncate ${LEVEL_COLOR[ev.level] ?? 'text-jarvis-text-dim'}`}>{ev.message}</span>
          </div>
        ))}
      </div>

      {latencyLines.length > 0 && (
        <>
          <div className="border-t border-jarvis-border" />
          <div className="flex flex-col gap-0.5 max-h-16 overflow-y-auto text-jarvis-text-faint">
            {latencyLines.slice(-4).map((line, i) => <span key={`${line}-${i}`}>{line}</span>)}
          </div>
        </>
      )}
    </div>
  )
}
