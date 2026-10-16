// A fixed statement block (docs/DESIGN.md 4.1): bordered, in body text, never collapsed and never in
// an attribute. Only constants from fixedTexts.ts / readiness.ts / messages.ts go in here, never
// sidecar, file or model text. Presentational: props only.

import type { ReactNode } from "react";

export function Statement({ children }: { children: ReactNode }) {
  return (
    <p className="alibi-block border-2 border-border bg-statement-bg px-2 py-1 text-statement-text">
      {children}
    </p>
  );
}
