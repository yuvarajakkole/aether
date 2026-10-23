import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Shell } from './components/Shell'
import { ToastProvider } from './components/ui'
import './index.css'
import { AgentNewPage } from './pages/AgentNewPage'
import { AgentPage } from './pages/AgentPage'
import { AgentsPage } from './pages/AgentsPage'
import { ComparePage } from './pages/ComparePage'
import { ModelsPage } from './pages/ModelsPage'
import { ReviewPage } from './pages/ReviewPage'
import { RunNewPage } from './pages/RunNewPage'
import { RunPage } from './pages/RunPage'
import { RunsPage } from './pages/RunsPage'
import { SettingsPage } from './pages/SettingsPage'
import { SuiteNewPage } from './pages/SuiteNewPage'
import { SuitePage } from './pages/SuitePage'

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 5000, refetchOnWindowFocus: false, retry: 1 } } })

function NotFound() {
  return (
    <div className="page empty">
      <h3>Page not found</h3>
      <p>The link may be out of date. Head back to your agents.</p>
      <a className="btn ink" href="/">Go to agents</a>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <BrowserRouter>
          <ErrorBoundary>
          <Routes>
            <Route element={<Shell />}>
              <Route index element={<AgentsPage />} />
              <Route path="agents/new" element={<AgentNewPage />} />
              <Route path="agents/:id" element={<AgentPage />} />
              <Route path="agents/:id/suites/new" element={<SuiteNewPage />} />
              <Route path="suites/:id" element={<SuitePage />} />
              <Route path="runs" element={<RunsPage />} />
              <Route path="runs/new" element={<RunNewPage />} />
              <Route path="runs/:id" element={<RunPage />} />
              <Route path="compare" element={<ComparePage />} />
              <Route path="review" element={<ReviewPage />} />
              <Route path="models" element={<ModelsPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
          </ErrorBoundary>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
)
