import { motion, AnimatePresence } from 'framer-motion'

interface Props {
  active: boolean
  frame: string | null
  focus: { box: unknown; label: string } | null
}

export default function CameraPanel({ active, frame, focus }: Props) {
  return (
    <AnimatePresence>
      {active && (
        <motion.div
          key="camera"
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          className="glass rounded-lg overflow-hidden"
        >
          <div className="flex items-center justify-between px-3 py-2 border-b border-jarvis-border">
            <span className="text-[10px] font-mono text-jarvis-yellow uppercase tracking-widest">
              Camera Active
            </span>
            <motion.div
              animate={{ opacity: [1, 0.3, 1] }}
              transition={{ repeat: Infinity, duration: 1.2 }}
              className="w-2 h-2 rounded-full bg-jarvis-red"
            />
          </div>

          {frame ? (
            <div className="relative">
              <img
                src={`data:image/jpeg;base64,${frame}`}
                alt="Camera preview"
                className="w-full object-cover max-h-48"
              />
              {focus && (
                <div className="absolute bottom-2 left-2 text-[10px] font-mono text-jarvis-yellow
                  bg-black/60 px-2 py-1 rounded">
                  {focus.label}
                </div>
              )}
            </div>
          ) : (
            <div className="flex items-center justify-center h-32 text-jarvis-text-faint text-xs font-mono">
              Waiting for frame…
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  )
}
