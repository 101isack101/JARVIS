import './index.css'
import { useJarvis } from './useJarvis'
import StatusBar from './components/StatusBar'
import NucleusCore from './components/NucleusCore'
import Transcript from './components/Transcript'
import ThoughtLog from './components/ThoughtLog'
import Telemetry from './components/Telemetry'
import ApprovalModal from './components/ApprovalModal'
import CameraPanel from './components/CameraPanel'

export default function App() {
  const { ui, resolveApproval } = useJarvis()

  const approve = () => {
    if (ui.pendingApproval) resolveApproval(ui.pendingApproval.id, true, ui.uiToken)
  }
  const reject = () => {
    if (ui.pendingApproval) resolveApproval(ui.pendingApproval.id, false, ui.uiToken)
  }

  return (
    <div className="flex flex-col h-full bg-jarvis-bg">
      <StatusBar
        version={ui.version}
        state={ui.state}
        mode={ui.mode}
        connectionStatus={ui.connectionStatus}
      />

      {/* Main two-column bento */}
      <div className="flex flex-1 gap-3 p-3 min-h-0 overflow-hidden">

        {/* Left: nucleus + transcript */}
        <div className="flex flex-col flex-1 gap-3 min-h-0">
          <NucleusCore state={ui.state} audioTelemetry={ui.audioTelemetry} />
          <Transcript
            inputTranscript={ui.inputTranscript}
            outputTranscript={ui.outputTranscript}
          />
        </div>

        {/* Right: thought log + telemetry + camera */}
        <div className="flex flex-col w-72 gap-3 shrink-0 overflow-y-auto">
          <ThoughtLog
            agentTools={ui.agentTools}
            events={ui.events}
            latencyLines={ui.latencyLines}
          />
          <Telemetry budget={ui.budget} memory={ui.memory} />
          <CameraPanel
            active={ui.cameraActive}
            frame={ui.cameraFrame}
            focus={ui.cameraFocus}
          />
        </div>
      </div>

      {/* HITL approval modal */}
      <ApprovalModal
        approval={ui.pendingApproval}
        onApprove={approve}
        onReject={reject}
      />
    </div>
  )
}
