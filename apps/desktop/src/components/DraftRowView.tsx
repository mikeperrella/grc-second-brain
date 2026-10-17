// One row of the review queue (ADR-021 decisions 3, 4 and 7; docs/DESIGN.md 4.1, 4.3, 4.4, 5).
// Presentational: props only.
//
// The status is the RUN RECORD's (`row.status`); no cell text is ever passed to the status chip.
// Fixed statements and reasons are always shown in full. The question, the answer and the quote
// are untrusted text: they go through LongText (plain text nodes, collapsed when long) and the
// citation is plain text, never a link.

import {
  EDITED_ANSWER_LINE,
  EDITED_EVIDENCE_LINE,
  EDITED_STATUS_LINE,
  RECORDED_BY_ALIBI,
  REVIEW_SENTENCE,
  UNREVIEWED_DRAFT_BADGE,
} from "../fixedTexts";
import { lineRangeText, reasonCode, showsReviewSentence, type DraftRow } from "../reviewQueue";
import { LongText } from "./LongText";
import { PlainText } from "./PlainText";
import { Statement } from "./Statement";
import { StatusChip } from "./StatusChip";

function Reasons({ reasons }: { reasons: readonly string[] }) {
  if (reasons.length === 0) {
    return <p>Reasons: none recorded.</p>;
  }
  return (
    <div>
      <p className="font-semibold">Reasons:</p>
      <ul className="list-disc pl-6">
        {reasons.map((reason, index) => {
          const code = reasonCode(reason);
          return (
            <li key={`${index}:${reason}`}>
              {code === null ? "unrecognised reason" : <code className="font-mono">{code}</code>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function DraftRowView({ row }: { row: DraftRow }) {
  const citation = row.citation;
  return (
    <li className="alibi-block grid gap-2 border border-border p-3" data-row={row.row}>
      <p className="font-semibold">Row {row.row}</p>
      <div>
        <p className="font-semibold">Question:</p>
        <LongText text={row.question_text} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <StatusChip value={row.status} />
        {row.status_edited ? <span className="font-semibold">{RECORDED_BY_ALIBI}</span> : null}
      </div>
      {row.status_edited ? <Statement>{EDITED_STATUS_LINE}</Statement> : null}

      <Reasons reasons={row.reasons} />

      <div className="grid gap-1">
        {row.answer_text !== null ? (
          <>
            <p className="font-semibold">
              Draft answer (unreviewed draft text){" "}
              <span className="alibi-block border-2 border-border bg-badge-bg px-2 py-1 text-badge-text">
                {UNREVIEWED_DRAFT_BADGE}
              </span>
            </p>
            <LongText text={row.answer_text} />
          </>
        ) : (
          <p>No draft answer was written for this row.</p>
        )}
        {row.answer_edited ? <Statement>{EDITED_ANSWER_LINE}</Statement> : null}
      </div>

      {citation !== null ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <p className="font-semibold">Source:</p>
            <p>
              <PlainText text={citation.source_path} />
            </p>
            <p>{lineRangeText(citation.start_line, citation.end_line)}</p>
          </div>
          <div>
            <p className="font-semibold">Quote:</p>
            <LongText text={citation.quote} />
          </div>
        </div>
      ) : (
        <p>No citation was recorded for this row.</p>
      )}
      {row.evidence_edited ? <Statement>{EDITED_EVIDENCE_LINE}</Statement> : null}
      {showsReviewSentence(row) ? <Statement>{REVIEW_SENTENCE}</Statement> : null}
    </li>
  );
}
