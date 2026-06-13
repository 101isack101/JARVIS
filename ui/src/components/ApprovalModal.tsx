import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { ApprovalPayload } from '../types'

interface Props {
  approval: ApprovalPayload | null
  onApprove: () => void
  onReject: () => void
}

interface ApprovalDialogProps {
  approval: ApprovalPayload
  onApprove: () => void
  onReject: () => void
}

const RISK_COLOR: Record<string, string> = {
  low:    'text-jarvis-green  border-jarvis-green',
  medium: 'text-jarvis-yellow border-jarvis-yellow',
  high:   'text-jarvis-red    border-jarvis-red',
}

export default function ApprovalModal({ approval, onApprove, onReject }: Props) {
  return (
    <AnimatePresence>
      {approval && (
        <ApprovalDialog
          key={approval.id}
          approval={approval}
          onApprove={onApprove}
          onReject={onReject}
        />
      )}
    </AnimatePresence>
  )
}

function ApprovalDialog({ approval, onApprove, onReject }: ApprovalDialogProps) {
  const [remaining, setRemaining] = useState(() => Math.ceil(approval.timeout_s))

  useEffect(() => {
    const startedAt = performance.now()
    const timeoutMs = approval.timeout_s * 1000
    const iv = setInterval(() => {
      const elapsed = performance.now() - startedAt
      setRemaining(Math.max(0, Math.ceil((timeoutMs - elapsed) / 1000)))
    }, 1000)
    return () => clearInterval(iv)
  }, [approval.timeout_s])

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.92, y: 12 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.92, y: 12 }}
      transition={{ type: 'spring', stiffness: 400, damping: 30 }}
      className="fixed inset-0 z-50 flex items-end justify-center p-4 bg-black/60 backdrop-blur-sm"
    >
      <div className="glass w-full max-w-md rounded-2xl p-5 flex flex-col gap-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <span className="text-xs font-mono text-jarvis-text-faint uppercase tracking-widest">
            HITL Approval Required
          </span>
          <span className={`text-xs font-mono border rounded px-2 py-0.5 ${RISK_COLOR[approval.risk] ?? ''}`}>
            {approval.risk.toUpperCase()}
          </span>
        </div>

        {/* Title */}
        <div>
          <h3 className="text-base font-semibold text-jarvis-text">{approval.title}</h3>
          <p className="text-xs text-jarvis-text-dim mt-1 font-mono break-all">{approval.details}</p>
        </div>

        {/* Countdown bar */}
        <div className="h-1 rounded bg-jarvis-border overflow-hidden">
          <motion.div
            className="h-full bg-jarvis-accent"
            animate={{ width: `${(remaining / approval.timeout_s) * 100}%` }}
            transition={{ duration: 1, ease: 'linear' }}
          />
        </div>
        <span className="text-[10px] font-mono text-jarvis-text-faint text-right">
          Auto-reject in {remaining}s
        </span>

        {/* Buttons */}
        <div className="flex gap-3">
          <button
            onClick={onReject}
            className="flex-1 py-2 rounded-lg border border-jarvis-border text-jarvis-text-dim
              text-sm font-mono hover:border-jarvis-red hover:text-jarvis-red transition-colors"
          >
            Reject
          </button>
          <button
            onClick={onApprove}
            className="flex-1 py-2 rounded-lg bg-jarvis-accent text-white
              text-sm font-mono hover:bg-jarvis-accent-dim transition-colors"
          >
            Approve
          </button>
        </div>
      </div>
    </motion.div>
  )
}
