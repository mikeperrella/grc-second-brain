// A typed refusal or error as fixed words (docs/DESIGN.md 7). The text and the code come from
// messages.ts (closed sets); the only dynamic parts are model NAMES, shown as plain text nodes.
// Raw sidecar text never reaches this component. Presentational: props only.

import { NO_DOWNLOAD_STATEMENT, type Described } from "../messages";
import { PlainText } from "./PlainText";
import { Statement } from "./Statement";

export function Refusal({ failure }: { failure: Described }) {
  return (
    <div role="alert" className="alibi-block grid gap-1 border-2 border-status-error p-2">
      <p className="font-semibold text-status-error">{failure.text}</p>
      <p className="text-sm">
        Code: <code className="font-mono">{failure.code}</code>
      </p>
      {failure.models.length > 0 ? (
        <>
          <p>Missing models:</p>
          <ul className="list-disc pl-6">
            {failure.models.map((name, index) => (
              <li key={`${index}:${name}`}>
                <PlainText text={name} />
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {failure.showNoDownload ? <Statement>{NO_DOWNLOAD_STATEMENT}</Statement> : null}
    </div>
  );
}
