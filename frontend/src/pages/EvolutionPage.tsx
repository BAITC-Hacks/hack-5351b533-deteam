import { useState } from 'react'
import { api, type Patch, type PatchStage } from '../api'
import { BenchPanel } from '../components/evolution/BenchPanel'
import { CasesPanel } from '../components/evolution/CasesPanel'
import { PatchCard, PatchStatusBadge } from '../components/evolution/PatchCard'
import { PatchProgress, type Progress } from '../components/evolution/PatchProgress'
import { VersionsPanel } from '../components/evolution/VersionsPanel'
import { Badge, ErrorBox, Panel } from '../components/ui'
import { useResource } from '../hooks/useResource'
import { useCatalog } from '../lib/catalog-context'
import { dateTime } from '../lib/format'
import { useLiveEvents } from '../lib/live-context'

/**
 * Эволюция каталога (EVOLUTION.md): ошибка → кейс → модель предлагает минимальную правку →
 * регрессия до/после → супервизор применяет или отклоняет → новая версия каталога.
 */
export function EvolutionPage() {
  const { catalog, setVersion } = useCatalog()
  const cases = useResource(() => api.cases(), 'cases')
  const patches = useResource(() => api.patches(), 'patches')
  const versions = useResource(() => api.versions(), 'versions')
  const runs = useResource(() => api.benchRuns(), 'bench')

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [active, setActive] = useState<{ id: string; progress: Progress | null } | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  // Мок не хранит состояние: применённые/отклонённые патчи и удалённые кейсы помним локально
  const [overrides, setOverrides] = useState<Record<string, Partial<Patch>>>({})
  const [deleted, setDeleted] = useState<Set<string>>(new Set())
  const [bench, setBench] = useState<{ done: number; total: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const frozen = catalog?.version.frozen ?? false

  useLiveEvents((e) => {
    if (e.type === 'patch.progress' && active && e.patch_id === active.id) {
      setActive({ id: active.id, progress: { stage: e.stage as PatchStage, done: e.done as number, total: e.total as number, message: e.message as string } })
    }
    if (e.type === 'patch.ready') {
      patches.reload()
      setOpenId(e.patch_id as string)
      setActive(null)
    }
    if (e.type === 'catalog.changed') {
      versions.reload()
      patches.reload()
    }
    if (e.type === 'case.created') cases.reload()
    if (e.type === 'bench.progress') setBench({ done: e.done as number, total: e.total as number })
    if (e.type === 'bench.finished') {
      setBench(null)
      runs.reload()
    }
  })

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const allPatches = (patches.data ?? []).map((p) => ({ ...p, ...overrides[p.patch_id] }))
  const open = allPatches.find((p) => p.patch_id === openId) ?? null
  const visibleCases = (cases.data ?? []).filter((c) => !deleted.has(c.case_id))

  const propose = () =>
    run(async () => {
      const p = await api.proposePatch([...selected])
      setActive({ id: p.patch_id, progress: { stage: 'proposing' } })
      setOpenId(null)
      setSelected(new Set())
    })

  const apply = (id: string) =>
    run(async () => {
      const v = await api.applyPatch(id)
      setOverrides((o) => ({ ...o, [id]: { status: 'applied', applied_version: v.version } }))
      setVersion(v)
      versions.reload()
    })

  const reject = (id: string) =>
    run(async () => {
      await api.rejectPatch(id)
      setOverrides((o) => ({ ...o, [id]: { status: 'rejected' } }))
    })

  return (
    <div className="stack">
      <Panel
        title="Эволюция каталога"
        hint="Ошибка в трассировке → кейс → модель предлагает минимальную правку описаний и границ → регрессия до/после → вы решаете, применять ли"
        actions={
          catalog && (
            <span className="row gap">
              <span>
                текущая <strong>{catalog.version.version}</strong> <code className="muted">{catalog.version.hash}</code>
              </span>
              <button className={frozen ? 'primary' : ''} disabled={busy} onClick={() => run(async () => setVersion(await api.freeze(!frozen)))}>
                {frozen ? '🔓 Снять заморозку' : '🔒 Заморозить'}
              </button>
            </span>
          )
        }
      >
        {frozen ? (
          <div className="callout callout-warn">
            🔒 Каталог заморожен на время зачётного прогона: применение патчей и откат заблокированы, все ходы логируются с хэшем {catalog?.version.hash}.
          </div>
        ) : (
          <p className="muted small">Каталог открыт для изменений. Перед зачётным прогоном заморозьте его.</p>
        )}
      </Panel>

      <ErrorBox error={error ?? cases.error ?? patches.error ?? versions.error ?? runs.error} />

      <CasesPanel
        cases={visibleCases}
        selected={selected}
        onToggle={(id) => setSelected((s) => {
          const next = new Set(s)
          if (next.has(id)) next.delete(id)
          else next.add(id)
          return next
        })}
        onPropose={propose}
        onDelete={(id) => run(async () => {
          await api.deleteCase(id)
          setDeleted((d) => new Set(d).add(id))
        })}
        busy={busy || !!active}
        frozen={frozen}
      />

      {active && (
        <Panel title="Готовим патч" hint="Обычно 15–20 секунд: предложение, проверка правил, прогон до и после">
          <PatchProgress patchId={active.id} progress={active.progress} />
        </Panel>
      )}

      {open && <PatchCard patch={open} frozen={frozen} busy={busy} onApply={() => apply(open.patch_id)} onReject={() => reject(open.patch_id)} onClose={() => setOpenId(null)} />}

      <div className="split">
        <Panel title="Патчи" hint="Все предложенные правки каталога">
          {allPatches.length ? (
            <table className="table compact clickable">
              <tbody>
                {allPatches.map((p) => (
                  <tr key={p.patch_id} onClick={() => setOpenId(p.patch_id)} className={p.patch_id === openId ? 'row-selected' : ''}>
                    <td>
                      <strong>{p.patch_id}</strong>
                      <div className="muted small">{p.created_at && dateTime(p.created_at)}</div>
                    </td>
                    <td>
                      <PatchStatusBadge status={p.status} />
                    </td>
                    <td className="small">
                      {p.proposal?.rationale ?? '—'}
                      <div className="muted">
                        кейсы {p.case_ids.join(', ')} · база {p.base_version}
                        {p.applied_version && ` → ${p.applied_version}`}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">Патчей пока нет</p>
          )}
          {!open && allPatches.some((p) => p.status === 'ready') && (
            <p className="small">
              <Badge tone="warn">ждёт решения</Badge> Откройте патч, чтобы посмотреть дифф и метрики.
            </p>
          )}
        </Panel>
        <VersionsPanel versions={versions.data ?? []} frozen={frozen} busy={busy} onRollback={(v) => run(async () => { setVersion(await api.rollback(v)); versions.reload() })} />
      </div>

      <BenchPanel runs={runs.data ?? []} progress={bench} busy={busy} onStart={() => run(() => api.startBench())} />
    </div>
  )
}
