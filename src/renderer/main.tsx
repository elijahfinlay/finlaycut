import React from 'react'
import ReactDOM from 'react-dom/client'
import { Toaster } from 'sonner'
import App from './App'
import { ErrorBoundary } from './components/common/ErrorBoundary'
import './styles/globals.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
      <Toaster
        position="bottom-right"
        toastOptions={{
          style: {
            background: '#2a2a2e',
            border: '1px solid #3a3a3e',
            color: '#e0e0e0',
          },
        }}
      />
    </ErrorBoundary>
  </React.StrictMode>
)
