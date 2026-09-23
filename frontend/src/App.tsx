import { Navigate, NavLink, Route, Routes } from 'react-router'
import { Badge } from './components/ui'
import { CatalogProvider } from './lib/catalog'
import { useCatalog } from './lib/catalog-context'
import { LiveProvider } from './lib/live'
import { useLiveStatus } from './lib/live-context'
import { CallPage } from './pages/CallPage'
import { EvolutionPage } from './pages/EvolutionPage'
import { OperatorPage } from './pages/OperatorPage'
import { ScenariosPage } from './pages/ScenariosPage'
import { SessionPage } from './pages/SessionPage'
import { SupervisorPage } from './pages/SupervisorPage'

export default function App() {
  return (
    <LiveProvider>
      <CatalogProvider>
        <div className="app">
          <nav className="nav" aria-label="Основная навигация">
            <NavLink to="/supervisor" className="brand" aria-label="Voice Router — открыть супервизора">
              <span className="brand-mark" aria-hidden="true">
                <svg viewBox="0 0 32 32" fill="none">
                  <path d="M10 9h7a6 6 0 0 1 6 6v8M10 9v14" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
                  <circle cx="10" cy="9" r="3" fill="currentColor" />
                  <circle cx="10" cy="23" r="3" fill="currentColor" />
                  <circle cx="23" cy="23" r="3" fill="currentColor" />
                </svg>
              </span>
              <span>Voice Router</span>
            </NavLink>
            <NavLink to="/call">Звонок</NavLink>
            <NavLink to="/supervisor">Супервизор</NavLink>
            <NavLink to="/evolution">Эволюция</NavLink>
            <NavLink to="/operator">Оператор</NavLink>
            <NavLink to="/scenarios">Каталог</NavLink>
            <HeaderStatus />
          </nav>
          <main className="main">
            <Routes>
              <Route path="/" element={<Navigate to="/supervisor" replace />} />
              <Route path="/supervisor" element={<SupervisorPage />}>
                <Route path="sessions/:id" element={<SessionPage />} />
              </Route>
              <Route path="/scenarios" element={<ScenariosPage />} />
              <Route path="/call" element={<CallPage />} />
              <Route path="/evolution" element={<EvolutionPage />} />
              <Route path="/operator" element={<OperatorPage />} />
            </Routes>
          </main>
        </div>
      </CatalogProvider>
    </LiveProvider>
  )
}

const LIVE_LABEL = { open: 'онлайн', connecting: 'подключение…', closed: 'нет связи' } as const
const LIVE_TONE = { open: 'ok', connecting: 'neutral', closed: 'bad' } as const

/** Версия каталога и замок заморозки всегда на виду (EVOLUTION.md §6), рядом — связь с живым потоком */
function HeaderStatus() {
  const { catalog } = useCatalog()
  const live = useLiveStatus()
  const v = catalog?.version
  return (
    <span className="nav-status">
      {v && (
        <span title={`Хэш ${v.hash}${v.note ? ` · ${v.note}` : ''}`}>
          каталог <strong>{v.version}</strong> <code className="muted">{v.hash}</code> {v.frozen && <Badge tone="info">🔒 заморожен</Badge>}
        </span>
      )}
      <Badge tone={LIVE_TONE[live]} title="Живой поток событий WS /ws/supervisor">
        ● {LIVE_LABEL[live]}
      </Badge>
    </span>
  )
}
