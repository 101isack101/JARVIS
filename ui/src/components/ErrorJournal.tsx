import type { ErrorJournalEntry } from '../types'

interface Props {
  rows: ErrorJournalEntry[]
}

export default function ErrorJournal({ rows }: Props) {
  const visible = rows.slice(-8).reverse()
  return (
    <section className="glass rounded-lg p-3 flex flex-col gap-2 text-[11px] font-mono" aria-label="Error journal">
      <span className="text-jarvis-text-faint text-[10px] uppercase tracking-widest">Error journal</span>
      {visible.length === 0 && (
        <span className="text-jarvis-text-dim">Sin errores recientes</span>
      )}
      {visible.map((row, index) => (
        <div key={`${row.ts}-${index}`} className="min-w-0">
          <div className="flex gap-2 text-jarvis-text-faint">
            <span className={row.severity === 'error' ? 'text-jarvis-red' : 'text-jarvis-yellow'}>
              {row.severity}
            </span>
            <span className="truncate">{row.source}</span>
          </div>
          <div className="truncate text-jarvis-text-dim">{row.message || 'sin mensaje'}</div>
        </div>
      ))}
    </section>
  )
}
