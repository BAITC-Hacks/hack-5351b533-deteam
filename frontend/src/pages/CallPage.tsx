import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { api } from '../api'
import { DialogView } from '../components/call/DialogView'
import { FeedbackBar } from '../components/supervisor/FeedbackBar'
import { ScenarioPath } from '../components/supervisor/ScenarioPath'
import { TracePanel } from '../components/trace/TracePanel'
import { Empty, FlagBadges, LangBadge, OutcomeBadge, Panel } from '../components/ui'
import { usePolling } from '../hooks/usePolling'
import { duration, time } from '../lib/format'

/** Карточка звонка: лента тем, весь диалог слева, трассировка выбранной реплики справа */
export function CallPage() {
  const { id = '' } = useParams()
  const call = usePolling(() => api.getCall(id), id)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  if (call.error) return <Empty>{call.error}</Empty>
  if (!call.data) return <Empty>Загрузка…</Empty>

  const { summary, turns } = call.data
  // По умолчанию — последняя реплика: у идущего звонка так видно свежую трассировку
  const selected = turns.find((t) => t.id === selectedId) ?? turns.at(-1) ?? null

  return (
    <div className="stack">
      <Panel
        title={summary.session.client?.name ?? 'Анонимный звонок'}
        hint={`${summary.session.client?.summary ?? 'Клиент не определён'} · начало ${time(summary.session.started_at)} · длительность ${duration(summary.session.started_at, summary.last_activity_at)} · реплик ${summary.turns_count}/${summary.session.max_turns}`}
        actions={<Link to="/supervisor">← Все звонки</Link>}
      >
        <div className="row gap wrap">
          <OutcomeBadge outcome={summary.outcome} />
          {summary.langs.map((l) => (
            <LangBadge key={l} lang={l} />
          ))}
          <FlagBadges flags={summary.flags} />
        </div>
        <h3>Лента тем</h3>
        <ScenarioPath
          path={summary.scenario_path}
          selectedIndex={selected ? selected.index - 1 : undefined}
          onSelect={(i) => setSelectedId(turns[i].id)}
        />
      </Panel>

      <div className="split">
        <Panel title="Диалог" hint="Полный транскрипт. Под каждым ответом — что выбрал роутер. Клик — подробности справа" className="call-dialog">
          <DialogView turns={turns} selectedId={selected?.id ?? null} onSelect={setSelectedId} />
        </Panel>
        <TracePanel turn={selected} extra={selected && <FeedbackBar turn={selected} onSaved={call.reload} />} />
      </div>
    </div>
  )
}
