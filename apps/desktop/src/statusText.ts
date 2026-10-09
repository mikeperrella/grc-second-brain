import type { HealthView } from "./ipc/health";

/** The one line the window shows. Built only from fixed codes: never detail text. */
export function describeView(view: HealthView): string {
  switch (view.state) {
    case "starting":
      return "Sidecar: starting";
    case "healthy":
      return "Sidecar: healthy";
    case "failed":
      return `Sidecar: failed (${view.source}: ${view.code})`;
  }
}

/** The CSS class for the line (colours live in App.css; there are no inline styles). */
export function viewClass(view: HealthView): string {
  switch (view.state) {
    case "starting":
      return "status status-starting";
    case "healthy":
      return "status status-healthy";
    case "failed":
      return "status status-failed";
  }
}
