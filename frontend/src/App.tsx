import { NavLink, Route, Routes } from 'react-router'
import { USE_MOCK } from './api'
import { CallPage } from './pages/CallPage'
import { ScenariosPage } from './pages/ScenariosPage'
import { SimulatorPage } from './pages/SimulatorPage'
import { SupervisorPage } from './pages/SupervisorPage'

export default function App() {
  return (
    <div className="app">
      <nav className="nav">
        <span className="brand">Voice Router</span>
        <NavLink to="/" end>
          Симулятор звонка
        </NavLink>
        <NavLink to="/supervisor">Супервизор</NavLink>
        <NavLink to="/scenarios">Каталог сценариев</NavLink>
        {USE_MOCK && (
          <span className="mock-flag" title="Данные генерирует src/api/mock.ts, бэкенд не используется">
            MOCK-режим
          </span>
        )}
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<SimulatorPage />} />
          <Route path="/supervisor" element={<SupervisorPage />} />
          <Route path="/supervisor/calls/:id" element={<CallPage />} />
          <Route path="/scenarios" element={<ScenariosPage />} />
        </Routes>
      </main>
    </div>
  )
}
