import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'

interface Props {
  inputTranscript: string
  outputTranscript: string
}

export default function Transcript({ inputTranscript, outputTranscript }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [inputTranscript, outputTranscript])

  return (
    <div className="flex flex-col gap-2 px-4 pb-4 flex-1 overflow-y-auto min-h-0">
      {inputTranscript && (
        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-mono text-jarvis-cyan uppercase tracking-widest">Isaac</span>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="text-sm text-jarvis-text-dim leading-relaxed whitespace-pre-wrap"
          >
            {inputTranscript}
          </motion.div>
        </div>
      )}

      {outputTranscript && (
        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-mono text-jarvis-accent uppercase tracking-widest">JARVIS</span>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="text-sm leading-relaxed prose prose-invert prose-sm max-w-none
              [&_code]:font-mono [&_code]:text-jarvis-cyan [&_code]:bg-jarvis-panel [&_code]:px-1 [&_code]:rounded
              [&_pre]:!bg-jarvis-panel [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-jarvis-border
              [&_a]:text-jarvis-accent [&_a]:no-underline hover:[&_a]:underline"
          >
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                code(props: any) {
                  const { children, className, node, ...rest } = props
                  const match = /language-(\w+)/.exec(className || '')
                  const isBlock = node?.position?.start?.line !== node?.position?.end?.line
                  return isBlock && match ? (
                    <SyntaxHighlighter
                      style={oneDark}
                      language={match[1]}
                      PreTag="div"
                      customStyle={{ margin: 0, background: 'transparent', fontSize: '0.8rem' }}
                      {...rest}
                    >
                      {String(children).replace(/\n$/, '')}
                    </SyntaxHighlighter>
                  ) : (
                    <code className={className} {...rest}>{children}</code>
                  )
                },
              }}
            >
              {outputTranscript}
            </ReactMarkdown>
          </motion.div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )
}
