import type { Patch } from '../../api'
import { useCatalog } from '../../lib/catalog-context'
import { dateTime, pct } from '../../lib/format'
import { Icon } from '../Icon'
import { Badge, Panel } from '../ui'

const STATUS: Record<Patch['status'], { label: string; tone: 'ok' | 'warn' | 'bad' | 'info' | 'neutral' }> = {
  proposing: { label: 'предлагается', tone: 'info' },
  validating: { label: 'проверка', tone: 'info' },
  regression: { label: 'регрессия', tone: 'info' },
  ready: { label: 'готов к применению', tone: 'warn' },
  applied: { label: 'применён', tone: 'ok' },
  rejected: { label: 'отклонён', tone: 'neutral' },
  failed: { label: 'ошибка', tone: 'bad' },
}

export function PatchStatusBadge({ status }: { status: Patch['status'] }) {
  const s = STATUS[status] ?? { label: status, tone: 'neutral' as const }
  return <Badge tone={s.tone}>{s.label}</Badge>
}

const FIELD_LABEL: Record<string, string> = {
  description: 'описание',
  not_this_if: 'правила границы (not_this_if)',
  'examples.ru': 'примеры RU',
  'examples.kk': 'примеры KZ',
}

const asItems = (v: unknown): unknown[] => (Array.isArray(v) ? v : v === null || v === undefined ? [] : [v])
const key = (v: unknown) => JSON.stringify(v)

function show(v: unknown) {
  if (typeof v === 'string') return v
  if (v && typeof v === 'object' && 'condition' in v) {
    const r = v as { condition: string; use_instead?: string }
    return `${r.condition}${r.use_instead ? ` → ${r.use_instead}` : ''}`
  }
  return JSON.stringify(v)
}

/** Дифф поля: что добавилось (+), что ушло (−), что осталось */
function FieldDiff({ before, after }: { before: unknown; after: unknown }) {
  const b = asItems(before)
  const a = asItems(after)
  const bKeys = new Set(b.map(key))
  const aKeys = new Set(a.map(key))
  const rows = [
    ...a.map((v) => ({ v, kind: bKeys.has(key(v)) ? 'same' : 'add' })),
    ...b.filter((v) => !aKeys.has(key(v))).map((v) => ({ v, kind: 'del' })),
  ]
  return (
    <ul className="diff">
      {rows.map((r, i) => (
        <li key={i} className={`diff-${r.kind}`}>
          <span className="diff-mark">{r.kind === 'add' ? '+' : r.kind === 'del' ? '−' : ' '}</span>
          {show(r.v)}
        </li>
      ))}
    </ul>
  )
}

function Delta({ before, after }: { before?: number; after?: number }) {
  if (before === undefined || after === undefined) return <span className="muted">—</span>
  const d = after - before
  const tone = d > 0.0005 ? 'text-ok' : d < -0.0005 ? 'text-bad' : 'muted'
  return (
    <span className={`num ${tone}`}>
      {d > 0 ? '+' : d < 0 ? '−' : '±'}
      {(Math.abs(d) * 100).toFixed(1)} п.п.
    </span>
  )
}

/** Карточка патча: зачем, что меняется в каталоге, что стало с метриками, кнопки применить/отклонить */
export function PatchCard(props: { patch: Patch; frozen: boolean; busy: boolean; onApply: () => void; onReject: () => void; onClose?: () => void }) {
  const { patch: p } = props
  const { scenarioName } = useCatalog()
  const reg = p.regression
  const canDecide = p.status === 'ready'

  return (
    <Panel
      title={
        <>
          Патч {p.patch_id} <PatchStatusBadge status={p.status} />
        </>
      }
      hint={`база ${p.base_version}${p.applied_version ? ` → ${p.applied_version}` : ''} · кейсы ${p.case_ids.join(', ')}${p.created_at ? ` · ${dateTime(p.created_at)}` : ''}`}
      actions={props.onClose && <button onClick={props.onClose}>Закрыть</button>}
      className="patch"
    >
      {p.proposal ? (
        <>
          <h3>Почему</h3>
          <p>{p.proposal.rationale}</p>
        </>
      ) : (
        <p className="muted">Предложение ещё готовится</p>
      )}

      {p.diff && p.diff.length > 0 && (
        <>
          <h3>Что меняется в каталоге</h3>
          {p.diff.map((d, i) => (
            <div key={i} className="diff-block">
              <div className="small">
                <strong>{scenarioName(d.scenario_id)}</strong> <code className="muted">{d.scenario_id}</code> · {FIELD_LABEL[d.field] ?? d.field}
              </div>
              <FieldDiff before={d.before} after={d.after} />
            </div>
          ))}
        </>
      )}

      {reg && (
        <>
          <h3>Регрессия: dev-набор + кейсы</h3>
          <table className="table compact">
            <thead>
              <tr>
                <th>Метрика</th>
                <th className="right">До</th>
                <th className="right">После</th>
                <th className="right">Изменение</th>
              </tr>
            </thead>
            <tbody>
              {(['primary_acc', 'full_match'] as const).map((m) => (
                <tr key={m}>
                  <td>
                    {m === 'primary_acc' ? 'Основной сценарий угадан' : 'Все сценарии угаданы'} <code className="muted small">{m}</code>
                  </td>
                  <td className="right num">{reg.before?.[m] !== undefined ? pct(reg.before[m]!) : '—'}</td>
                  <td className="right num">{reg.after?.[m] !== undefined ? pct(reg.after[m]!) : '—'}</td>
                  <td className="right">
                    <Delta before={reg.before?.[m]} after={reg.after?.[m]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row gap wrap">
            <span className="small">Починено:</span>
            {reg.fixed?.length ? reg.fixed.map((id) => <Badge key={id} tone="ok"><Icon name="check" size={14} />{id}</Badge>) : <span className="muted small">—</span>}
            <span className="small">Сломано:</span>
            {reg.broken?.length ? reg.broken.map((id) => <Badge key={id} tone="bad"><Icon name="close" size={14} />{id}</Badge>) : <Badge tone="ok">ничего</Badge>}
          </div>
        </>
      )}

      {p.validator_log && p.validator_log.length > 0 && (
        <>
          <h3>Лог валидатора</h3>
          <ul className="log">
            {p.validator_log.map((l, i) => (
              <li key={i}>
                <code>{l}</code>
              </li>
            ))}
          </ul>
        </>
      )}

      {canDecide && (
        <div className="row gap patch-actions">
          <button className="primary" disabled={props.frozen || props.busy} onClick={props.onApply}>
            Применить
          </button>
          <button disabled={props.busy} onClick={props.onReject}>
            Отклонить
          </button>
          {props.frozen && <span className="small text-warn">Каталог заморожен — сначала снимите заморозку</span>}
        </div>
      )}
    </Panel>
  )
}
