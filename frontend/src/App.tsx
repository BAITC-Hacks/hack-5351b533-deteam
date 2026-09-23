import { Navigate, NavLink, Route, Routes } from 'react-router'
import { Badge } from './components/ui'
import { CatalogProvider } from './lib/catalog'
import { useCatalog } from './lib/catalog-context'
import { LiveProvider } from './lib/live'
import { useLiveStatus } from './lib/live-context'
import { CallPage } from './pages/CallPage'
import { ScenariosPage } from './pages/ScenariosPage'
import { SessionPage } from './pages/SessionPage'
import { SupervisorPage } from './pages/SupervisorPage'

export default function App() {
  return (
    <LiveProvider>
      <CatalogProvider>
        <div className="app">
          <nav className="nav">
            <span className="brand">Voice Router</span>
            <NavLink to="/supervisor">Супервизор</NavLink>
            <NavLink to="/scenarios">Каталог сценариев</NavLink>
            <NavLink to="/call">Звонок</NavLink>
            <HeaderStatus />
          </nav>
          <main className="main">
            <Routes>
              <Route path="/" element={<Navigate to="/supervisor" replace />} />
              <Route path="/supervisor" element={<SupervisorPage />} />
              <Route path="/supervisor/sessions/:id" element={<SessionPage />} />
              <Route path="/scenarios" element={<ScenariosPage />} />
              <Route path="/call" element={<CallPage />} />
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
