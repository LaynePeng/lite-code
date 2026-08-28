import React from "react";

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  message: string;
}

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, message: "" };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error.message || String(error) };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[ErrorBoundary] 渲染异常:", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="crash-screen">
          <div className="crash-icon">💥</div>
          <h2>界面渲染出错</h2>
          <p className="crash-message">{this.state.message}</p>
          <div className="crash-actions">
            <button onClick={() => window.location.reload()}>🔄 重新加载</button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}