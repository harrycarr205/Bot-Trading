import { Component, type ReactNode } from "react";

interface Props { children: ReactNode; }
interface State { error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return <div className="loss" style={{ padding: 16 }}>Data unavailable — {this.state.error.message}</div>;
    }
    return this.props.children;
  }
}
