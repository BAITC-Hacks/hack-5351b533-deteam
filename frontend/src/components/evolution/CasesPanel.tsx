import { Link } from 'react-router'
import type { Case } from '../../api'
import { dateTime } from '../../lib/format'
import { Icon } from '../Icon'
import { Badge, Empty, LangBadge, Panel, ScenarioChip } from '../ui'

const SOURCE_LABEL: Record<Case['source'], string> = { supervisor: 'супервизор', synthetic: 'синтетика', dev: 'dev-набор' }

/** Кейсы регрессии: ошибки роутера, отмеченные супервизором или найденные синтетикой. Выбранные уходят в патч */
export function CasesPanel(props: {
  cases: Case[]
  selected: Set<string>
  onToggle: (id: string) => void
  onPropose: () => void
  onDelete: (id: string) => void
  busy: boolean
  frozen: boolean
}) {
  const { cases, selected } = props
  return (
    <Panel
      title={`Кейсы · ${cases.length}`}
      actions={
        <button className="primary" disabled={!selected.size || props.busy} onClick={props.onPropose} title={props.frozen ? 'Предложить можно, применить — только после снятия заморозки' : undefined}>
          Предложить патч{selected.size ? ` · ${selected.size}` : ''}
        </button>
      }
    >
      {cases.length ? (
        <table className="table">
          <thead>
            <tr>
              <th />
              <th>Реплика</th>
              <th>Нужно было</th>
              <th>Роутер выбрал</th>
              <th>Источник</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <tr key={c.case_id} className={selected.has(c.case_id) ? 'row-selected' : ''}>
                <td>
                  <input type="checkbox" checked={selected.has(c.case_id)} onChange={() => props.onToggle(c.case_id)} aria-label={`Выбрать ${c.case_id}`} />
                </td>
                <td>
                  «{c.text}» <LangBadge lang={c.lang} />
                  <div className="muted small">
                    <code>{c.case_id}</code> · {dateTime(c.created_at)}
                    {c.session_id && (
                      <>
                        {' · '}
                        <Link to={`/supervisor/sessions/${encodeURIComponent(c.session_id)}?turn=${c.turn ?? ''}`}>
                          {c.session_id}, ход {c.turn}
                        </Link>
                      </>
                    )}
                  </div>
                  {c.note && <div className="small">{c.note}</div>}
                </td>
                <td>
                  <span className="row gap wrap">
                    {c.expected.map((id, i) => (
                      <ScenarioChip key={id} id={id} title={i === 0 ? 'основной' : undefined} />
                    ))}
                  </span>
                </td>
                <td>
                  <span className="row gap wrap">{c.observed?.length ? c.observed.map((id) => <ScenarioChip key={id} id={id} />) : '—'}</span>
                  {c.observed && c.observed.join() === c.expected.join() && <Badge tone="ok">уже верно</Badge>}
                </td>
                <td>
                  <Badge>{SOURCE_LABEL[c.source] ?? c.source}</Badge>
                </td>
                <td>
                  <button className="chip-x" title="Удалить кейс" aria-label={`Удалить кейс ${c.case_id}`} onClick={() => props.onDelete(c.case_id)}>
                    <Icon name="close" size={14} spacing="none" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>Кейсов нет. Их создают кнопкой «Неверно» в карточке звонка</Empty>
      )}
    </Panel>
  )
}
