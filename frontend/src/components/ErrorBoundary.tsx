import { Component, type ErrorInfo, type ReactNode } from 'react'

type State = { error: Error | null }

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Aether UI error', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="page empty">
        <h3>This page hit an error</h3>
        <p>Your data is safe; only the screen failed to render. Reload to try again, and include this message if you report it: {this.state.error.message}</p>
        <button className="btn ink" onClick={() => location.reload()}>Reload page</button>
      </div>
    )
  }
}
