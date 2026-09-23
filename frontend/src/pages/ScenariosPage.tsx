import { useEffect, useMemo, useState } from 'react'
import { api, type Scenario } from '../api'
import { Badge, Empty, Panel } from '../components/ui'

/** Каталог сценариев (scenarios.json). Пока только просмотр; редактирование без разработчиков — опционально по ТЗ */
export function ScenariosPage() {
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    api.listScenarios().then(setScenarios)
  }, [])

  const filtered = useMemo(() => {
    const q = query.toLowerCase()
    return scenarios.filter((s) => (s.id + s.title + s.description).toLowerCase().includes(q))
  }, [scenarios, query])

  const selected = scenarios.find((s) => s.id === selectedId) ?? filtered[0] ?? null

  return (
    <div className="split">
      <Panel title={`Сценарии (${scenarios.length})`} hint="Между чем выбирает роутер">
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Поиск по названию или id…" />
        <ul className="list">
          {filtered.map((s) => (
            <li key={s.id} className={s.id === selected?.id ? 'list-active' : ''} onClick={() => setSelectedId(s.id)}>
              <div>{s.title}</div>
              <code className="muted small">{s.id}</code>
            </li>
          ))}
        </ul>
      </Panel>

      {selected ? (
        <Panel
          title={selected.title}
          hint={selected.id}
          actions={
            <button disabled title="TODO: редактирование каталога">
              Редактировать
            </button>
          }
        >
          <p>{selected.description}</p>
          {selected.irreversible && <Badge tone="warn">Необратимое действие — нужно подтверждение клиента</Badge>}

          <h3>Границы с соседними сценариями</h3>
          <p>{selected.boundaries}</p>

          <h3>Параметры</h3>
          <table className="kv">
            <tbody>
              {selected.params.map((p) => (
                <tr key={p.name}>
                  <td>
                    <code>{p.name}</code>
                  </td>
                  <td>
                    {p.description} {p.required && <Badge tone="info">обязательный</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Действия</h3>
          <p>{selected.actions.map((a) => <code key={a}>{a} </code>)}</p>

          <h3>Примеры</h3>
          <div className="split">
            <div>
              <Badge>RU</Badge>
              <ul>{selected.examples.ru.map((e) => <li key={e}>{e}</li>)}</ul>
            </div>
            <div>
              <Badge>KZ</Badge>
              <ul>{selected.examples.kk.map((e) => <li key={e}>{e}</li>)}</ul>
            </div>
          </div>
        </Panel>
      ) : (
        <Empty>Сценарий не выбран</Empty>
      )}
    </div>
  )
}
