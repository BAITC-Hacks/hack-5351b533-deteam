import { Navigate, NavLink, Route, Routes } from 'react-router'
import { Icon } from './components/Icon'
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
            <NavLink to="/supervisor" className="brand" aria-label="NotSoFar v.7 — открыть супервизора">
              <img className="brand-logo" src="/notsofar-mark.png?v=2" width="84" height="56" alt="" aria-hidden="true" />
              <span className="brand-wordmark">NotSoFar <span className="brand-version">v.7</span></span>
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

function HeaderStatus() {
  const { catalog } = useCatalog()
  const live = useLiveStatus()
  return (
    <span className="nav-status">
      {catalog?.version.frozen && <Badge tone="info"><Icon name="lock" size={14} /> Каталог заморожен</Badge>}
      <Badge tone={LIVE_TONE[live]} title="Живой поток событий WS /ws/supervisor">
        <Icon name="statusDot" size={8} />{LIVE_LABEL[live]}
      </Badge>
    </span>
  )
}
