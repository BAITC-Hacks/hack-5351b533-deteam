import { useState } from 'react'
import { Badge, Empty, ErrorBox, Panel } from '../components/ui'
import { useCatalog } from '../lib/catalog-context'
import { dateTime, pct } from '../lib/format'

const PRIORITY_TONE = { normal: 'neutral', high: 'warn', urgent: 'bad' } as const

/** Каталог сценариев текущей версии (GET /api/catalog) — между чем выбирает роутер */
export function ScenariosPage() {
  const { catalog, error } = useCatalog()
  const [query, setQuery] = useState('')

  if (error) return <ErrorBox error={error} />
  if (!catalog) return <Empty>Загрузка…</Empty>

  const q = query.toLowerCase()
  const rows = catalog.scenarios.filter((s) => `${s.scenario_id} ${s.name_ru} ${s.name} ${s.domain} ${s.category}`.toLowerCase().includes(q))
  const domains = [...new Set(rows.map((s) => s.domain))]
  const v = catalog.version

  return (
    <div className="stack">
      <Panel
        title={`Каталог ${v.version}`}
        hint={`хэш ${v.hash} · от ${dateTime(v.created_at)}${v.parent ? ` · родитель ${v.parent}` : ''}${v.applied_patch ? ` · патч ${v.applied_patch}` : ''}`}
        actions={
          <span className="row gap">
            {v.frozen && <Badge tone="info">🔒 заморожен</Badge>}
            {v.primary_acc != null && <Badge tone="ok">точность {pct(v.primary_acc)}</Badge>}
          </span>
        }
      >
        {v.note && <p>{v.note}</p>}
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Поиск по названию, id, домену…" />
      </Panel>

      {domains.map((d) => (
        <Panel key={d} title={d}>
          <table className="table compact">
            <thead>
              <tr>
                <th>id</th>
                <th>Сценарий</th>
                <th>Категория</th>
                <th>Приоритет</th>
                <th>Особенности</th>
              </tr>
            </thead>
            <tbody>
              {rows
                .filter((s) => s.domain === d)
                .map((s) => (
                  <tr key={s.scenario_id}>
                    <td>
                      <code>{s.scenario_id}</code>
                    </td>
                    <td>
                      {s.name_ru}
                      <div className="muted small">{s.name}</div>
                    </td>
                    <td>{s.category}</td>
                    <td>
                      <Badge tone={PRIORITY_TONE[s.priority] ?? 'neutral'}>{s.priority}</Badge>
                    </td>
                    <td>
                      <span className="row gap wrap">
                        {s.fast_path_eligible && <Badge tone="ok">быстрый путь</Badge>}
                        {s.requires_identification && <Badge>нужна идентификация</Badge>}
                        {s.requires_confirmation && <Badge tone="warn">нужно подтверждение</Badge>}
                      </span>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </Panel>
      ))}
      {!rows.length && <Empty>Ничего не найдено</Empty>}
    </div>
  )
}
