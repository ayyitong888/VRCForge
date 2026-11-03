import React from "react";

type ErrorBoundaryProps = { children: React.ReactNode };
type ErrorBoundaryState = { error: Error | null };

/**
 * Root crash guard. React 18 unmounts the whole tree on an uncaught render
 * error, which renders as a blank window ("white screen") until the user
 * reloads. This boundary keeps a visible failure card instead and logs the
 * error so the cause stays diagnosable.
 */
export class RootErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[vrcforge:render-crash]", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="flex h-screen items-center justify-center bg-workspace px-6 text-foreground">
          <div className="max-w-lg rounded-xl border border-destructive/40 bg-background p-6 text-center">
            <div className="font-semibold">VRCForge could not continue</div>
            <div className="mt-2 break-words text-sm text-muted-foreground">{String(this.state.error?.message || this.state.error)}</div>
            <button
              type="button"
              className="mt-4 rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted"
              onClick={() => window.location.reload()}
              data-error-boundary-reload
            >
              Reload
            </button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
