import { CallPanel } from '../components/call/CallPanel'
import { TracePanel } from '../components/trace/TracePanel'
import { useCallSession } from '../hooks/useCallSession'

/** Главный экран демо: слева клиент разговаривает с роботом, справа супервизор видит логику решения */
export function SimulatorPage() {
  const call = useCallSession()
  return (
    <div className="split">
      <CallPanel call={call} />
      <TracePanel turn={call.selectedTurn} />
    </div>
  )
}
