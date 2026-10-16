// Which models are ready (G11). Presentational: props only. Model names come from the sidecar's
// list of CONFIGURED models and are shown as plain text nodes in a monospace span; nothing here
// treats a name as markup or as a verdict. The no-download sentence is always visible when models
// are missing (DESIGN.md 7), never in a tooltip.

import { describeOllama, NO_DOWNLOAD_STATEMENT, type ReadinessView } from "../readiness";

const NAME_CLASS = "font-mono whitespace-pre-wrap [unicode-bidi:isolate]";

export function ReadinessIndicator({ view }: { view: ReadinessView }) {
  switch (view.state) {
    case "unknown":
      return <p>Models: not checked yet (ALIBI asks once the sidecar is healthy)</p>;
    case "checking":
      return <p role="status" aria-live="polite">Models: checking</p>;
    case "ready":
      return <p role="status" aria-live="polite">Models: ready</p>;
    case "unavailable":
      return (
        <p role="status" aria-live="polite">
          Models: could not be checked (<code className="font-mono">{view.code}</code>)
        </p>
      );
    case "degraded":
      return (
        <div role="status" aria-live="polite">
          <p className="font-semibold">Models: not ready</p>
          <p>{describeOllama(view.ollama)}</p>
          {view.models.length > 0 ? (
            <>
              <p>Missing models:</p>
              <ul className="list-disc pl-6">
                {view.models.map((name, index) => (
                  <li key={`${index}:${name}`}>
                    <span className={NAME_CLASS}>{name}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          <p className="alibi-block border-2 border-border bg-statement-bg px-2 py-1 text-statement-text">
            {NO_DOWNLOAD_STATEMENT}
          </p>
        </div>
      );
  }
}
