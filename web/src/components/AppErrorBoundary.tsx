import { Button, Result, Typography } from "antd";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { clearStaleChunkGuard, recoverFromStaleChunk } from "../lib/chunk-recovery";

/**
 * Route-level error boundary.
 *
 * Without this, any render-time throw leaves a permanently blank page with no
 * recovery and no telemetry — a single null timestamp in a crawled row was enough
 * to kill the whole app. This is the outermost net; `QueryBoundary` handles the
 * expected async failures closer to the data.
 *
 * It also absorbs the one class of error the user can fix with a refresh: a lazy
 * chunk that a redeploy deleted (see lib/chunk-recovery.ts).
 */

type Props = {
  children: ReactNode;
  /** Shown in the fallback so the user knows what broke. */
  label?: string;
  /** Changing this value resets the boundary — pass the route path. */
  resetKey?: string;
};

type State = { error: Error | null };

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    // Navigating away from a broken screen should clear the error.
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // A chunk deleted by a redeploy is expected during a rolling deploy: reload and
    // the new build's hashes are picked up. Swallow it rather than showing a dead end.
    if (recoverFromStaleChunk(error)) return;
    // eslint-disable-next-line no-console
    console.error("[AppErrorBoundary]", this.props.label ?? "render failed", error, info.componentStack);
  }

  componentDidMount() {
    // Reaching a mount means the loaded chunks are valid; allow a future recovery.
    clearStaleChunkGuard();
  }

  private reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <Result
        status="error"
        title="This screen could not be displayed"
        subTitle={
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            {this.props.label ? `Error on ${this.props.label}. ` : ""}
            {error.message || "An unexpected rendering error occurred."}
          </Typography.Paragraph>
        }
        extra={
          <Button type="primary" onClick={this.reset}>
            Try again
          </Button>
        }
      />
    );
  }
}