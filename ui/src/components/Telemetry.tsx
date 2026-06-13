import { motion } from 'framer-motion'
import type { BudgetPayload, MemoryStats } from '../types'

interface Props {
  budget: BudgetPayload | null
  memory: MemoryStats
}

const STATUS_COLOR: Record<string, string> = {
  ok:      'text-jarvis-green',
  warn:    'text-jarvis-yellow',
  alert:   'text-[#f97316]',
  blocked: 'text-jarvis-red',
}

const RING_COLOR: Record<string, string> = {
  ok:      '#10b981',
  warn:    '#f59e0b',
  alert:   '#f97316',
  blocked: '#ef4444',
}

function BudgetRing({ label, pct, status, spent, tokensLabel }: {
  label: string; pct: number; status: string; spent: number; limit?: number; tokensLabel: string
}) {
  const r = 28
  const circ = 2 * Math.PI * r
  const dashOffset = circ * (1 - Math.min(pct, 1))
  const color = RING_COLOR[status] ?? '#64748b'

  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative w-16 h-16">
        <svg className="w-full h-full -rotate-90" viewBox="0 0 64 64">
          <circle cx="32" cy="32" r={r} fill="none" stroke="#1e1e2e" strokeWidth="4" />
          <motion.circle
            cx="32" cy="32" r={r}
            fill="none"
            stroke={color}
            strokeWidth="4"
            strokeLinecap="round"
            strokeDasharray={circ}
            animate={{ strokeDashoffset: dashOffset }}
            transition={{ duration: 0.6, ease: 'easeOut' }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={`text-[9px] font-mono font-bold ${STATUS_COLOR[status]}`}>
            {(pct * 100).toFixed(0)}%
          </span>
        </div>
      </div>
      <div className="text-center">
        <div className="text-[9px] font-mono text-jarvis-text-faint uppercase tracking-widest">{label}</div>
        <div className="text-[9px] font-mono text-jarvis-text-dim">{tokensLabel}</div>
        <div className="text-[9px] font-mono text-jarvis-text-faint">${spent.toFixed(3)}</div>
      </div>
    </div>
  )
}

export default function Telemetry({ budget, memory }: Props) {
  if (!budget) return null

  return (
    <div className="glass rounded-lg p-3 flex flex-col gap-3">
      <span className="text-jarvis-text-faint text-[10px] font-mono uppercase tracking-widest">Budget · {budget.period}</span>

      {/* Rings */}
      <div className="flex justify-around">
        <BudgetRing
          label="Gemini"
          pct={budget.gemini.pct}
          status={budget.gemini.status}
          spent={budget.gemini.spentUsd}
          limit={budget.gemini.limitUsd}
          tokensLabel={budget.gemini.tokensLabel}
        />
        <BudgetRing
          label="Claude"
          pct={budget.claude.pct}
          status={budget.claude.status}
          spent={budget.claude.spentUsd}
          limit={budget.claude.limitUsd}
          tokensLabel={budget.claude.tokensLabel}
        />
      </div>

      {/* Total */}
      <div className="flex justify-between text-[10px] font-mono border-t border-jarvis-border pt-2">
        <span className="text-jarvis-text-faint">Total</span>
        <span className="text-jarvis-text">${budget.totalUsd.toFixed(4)}</span>
      </div>

      {/* Memory stats */}
      <div className="flex gap-3 text-[10px] font-mono border-t border-jarvis-border pt-2">
        <span className="text-jarvis-text-faint">Memory</span>
        <span className="text-jarvis-green ml-auto">{memory.ok} ok</span>
        {memory.active > 0 && <span className="text-jarvis-yellow">{memory.active} active</span>}
        {memory.error > 0  && <span className="text-jarvis-red">{memory.error} err</span>}
      </div>
    </div>
  )
}
