import React from "react";

export default class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Unhandled application error", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <section role="alert" className="grid min-h-[60vh] place-items-center bg-[rgb(var(--color-background))] p-6 text-[rgb(var(--color-text-primary))]">
        <section className="w-full max-w-lg rounded-2xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] p-8 text-center shadow-2xl">
          <h1 className="text-xl font-semibold">
            This page could not be displayed
          </h1>
          <p className="mt-3 text-sm text-[rgb(var(--color-text-secondary))]">
            This screen encountered an error. Try again or open another page
            from the sidebar. Reload if the problem continues.
          </p>
          <details className="mt-4 text-left text-xs">
            <summary className="cursor-pointer">Error details</summary>
            <p className="mt-2 break-words font-mono">{String(this.state.error?.message || this.state.error)}</p>
            <p className="mt-2 break-words">Page: {window.location.pathname}</p>
          </details>
          <button
            className="mt-6 mr-3 rounded-lg border px-4 py-2 text-sm font-semibold"
            onClick={() => this.setState({ error: null })}
            type="button"
          >
            Try again
          </button>
          <button
            className="mt-6 rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black"
            onClick={() => window.location.reload()}
            type="button"
          >
            Reload page
          </button>
        </section>
      </section>
    );
  }
}
