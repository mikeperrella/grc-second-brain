// Shown instead of a model-dependent action while the models are known to be missing
// (docs/DESIGN.md 7 "Model unavailable": exact missing names, the no-download sentence, and the
// button disabled with that reason). Presentational: props only.

import { NO_DOWNLOAD_STATEMENT, type ReadinessView } from "../readiness";
import { PlainText } from "./PlainText";
import { Statement } from "./Statement";

/** True when the readiness check has said that models are missing. */
export function modelsBlocked(view: ReadinessView): boolean {
  return view.state === "degraded";
}

export function ModelsBlock({ view }: { view: ReadinessView }) {
  if (view.state !== "degraded") {
    return null;
  }
  return (
    <div role="alert" className="alibi-block grid gap-1 border-2 border-status-error p-2">
      <p className="font-semibold text-status-error">
        This needs the models, and they are not ready, so the button below is off.
      </p>
      {view.models.length > 0 ? (
        <>
          <p>Missing models:</p>
          <ul className="list-disc pl-6">
            {view.models.map((name, index) => (
              <li key={`${index}:${name}`}>
                <PlainText text={name} />
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <Statement>{NO_DOWNLOAD_STATEMENT}</Statement>
    </div>
  );
}
