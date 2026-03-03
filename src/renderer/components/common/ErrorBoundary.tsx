import React from 'react'

interface Props {
  children: React.ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary] Uncaught error:', error, info.componentStack)
  }

  handleReload = () => {
    this.setState({ hasError: false, error: null })
  }

  handleHardReload = () => {
    window.location.reload()
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="h-screen flex items-center justify-center bg-resolve-bg p-8">
          <div className="max-w-md w-full bg-resolve-surface border border-resolve-border rounded-xl p-8 text-center space-y-4">
            <div className="w-12 h-12 mx-auto rounded-full bg-resolve-error/15 flex items-center justify-center">
              <svg className="w-6 h-6 text-resolve-error" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
              </svg>
            </div>
            <h2 className="text-lg font-semibold text-resolve-text">Something went wrong</h2>
            <p className="text-sm text-resolve-textDim">
              {this.state.error?.message || 'An unexpected error occurred.'}
            </p>
            <div className="flex gap-3 justify-center pt-2">
              <button
                onClick={this.handleReload}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-resolve-accent text-white hover:bg-resolve-accent/90 transition-colors"
              >
                Try Again
              </button>
              <button
                onClick={this.handleHardReload}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-resolve-surfaceLight text-resolve-textDim hover:text-resolve-text transition-colors"
              >
                Reload App
              </button>
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
