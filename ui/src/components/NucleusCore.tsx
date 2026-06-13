import { motion, AnimatePresence } from 'framer-motion'
import type { JarvisState, AudioTelemetry } from '../types'

interface Props {
  state: JarvisState
  audioTelemetry: AudioTelemetry | null
}

const STATE_RING: Record<JarvisState, string> = {
  idle:      'border-jarvis-border',
  listening: 'border-jarvis-cyan',
  thinking:  'border-jarvis-accent',
  speaking:  'border-jarvis-green',
  blocked:   'border-jarvis-red',
}

const STATE_GLOW: Record<JarvisState, string> = {
  idle:      '',
  listening: 'shadow-[0_0_24px_rgba(6,182,212,0.3)]',
  thinking:  'shadow-[0_0_24px_rgba(124,58,237,0.4)]',
  speaking:  'shadow-[0_0_24px_rgba(16,185,129,0.35)]',
  blocked:   'shadow-[0_0_24px_rgba(239,68,68,0.4)]',
}

export default function NucleusCore({ state, audioTelemetry }: Props) {
  const erle = audioTelemetry?.erlePeakDb ?? 0
  const wakeword = audioTelemetry?.wakewordPeak ?? 0
  const audioLevel = state === 'listening' ? Math.min(1, wakeword) : 0

  return (
    <div className="flex flex-col items-center justify-center gap-4 py-6">
      {/* Outer ring */}
      <div className="relative flex items-center justify-center">
        {/* Pulsing bg halo */}
        <AnimatePresence>
          {state === 'listening' && (
            <motion.div
              key="halo"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: [1, 1.15, 1], opacity: [0.2, 0.05, 0.2] }}
              exit={{ opacity: 0 }}
              transition={{ repeat: Infinity, duration: 2.4, ease: 'easeInOut' }}
              className="absolute w-32 h-32 rounded-full bg-jarvis-cyan"
            />
          )}
        </AnimatePresence>

        {/* Main nucleus */}
        <motion.div
          animate={
            state === 'thinking' ? { rotate: 360 } :
            state === 'speaking' ? { scale: [1, 1.06, 1] } :
            { scale: 1, rotate: 0 }
          }
          transition={
            state === 'thinking' ? { repeat: Infinity, duration: 3, ease: 'linear' } :
            state === 'speaking' ? { repeat: Infinity, duration: 1.6, ease: 'easeInOut' } :
            { duration: 0.4 }
          }
          className={`
            w-20 h-20 rounded-full border-2 flex items-center justify-center
            transition-colors duration-500
            ${STATE_RING[state]} ${STATE_GLOW[state]}
          `}
        >
          {/* Inner dot */}
          <motion.div
            animate={{ scale: state === 'blocked' ? [1, 1.3, 1] : 1 }}
            transition={{ repeat: state === 'blocked' ? Infinity : 0, duration: 0.8 }}
            className="w-8 h-8 rounded-full bg-jarvis-accent opacity-80"
          />
        </motion.div>
      </div>

      {/* Audio bars (visible in listening mode) */}
      <AnimatePresence>
        {state === 'listening' && (
          <motion.div
            key="bars"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="flex items-end gap-1 h-8"
          >
            {[0.3, 0.6, 1.0, 0.8, 0.4, 0.9, 0.5, 0.7, 0.3, 0.6].map((base, i) => (
              <motion.div
                key={i}
                animate={{ scaleY: [base, base + audioLevel * 0.6, base] }}
                transition={{ repeat: Infinity, duration: 0.6 + i * 0.07, ease: 'easeInOut' }}
                style={{ originY: 1, height: `${12 + base * 20}px` }}
                className="w-1 rounded-t bg-jarvis-cyan opacity-70"
              />
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Telemetry micro-labels */}
      {audioTelemetry && (
        <div className="flex gap-4 text-[10px] font-mono text-jarvis-text-faint">
          <span>AEC {erle.toFixed(0)} dB</span>
          <span>WW {(wakeword * 100).toFixed(0)}%</span>
        </div>
      )}
    </div>
  )
}
