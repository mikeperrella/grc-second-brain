// The Reality check screen (chunk 65j; ADR-016, ADR-022, threat model F26-7 and F43; docs/DESIGN.md
// 7, 8 and 11.1). Presentational: props only.
//
// READ-ONLY: nothing is written. The export is chosen from the Imports listing (names only: the
// window never sends a path). The person confirms the MFA types before the check can be sent;
// nothing is chosen for them. Every fixed statement is the window's own constant, never a string
// from the reply: the result is ONE of the two DESIGN.md 11.1 sentences, picked from the reply's
// `finding` word by reality.ts (`readResult`), and the three sidecar statements always show with a
// result. When the reply does not hold together (counts against the finding, or two different file
// hashes) there is an error block and no sentence.
//
// The logins are personal data (F26-7): the 11.1 row is always above the list, the logins are plain
// text (never a link), they are not stored or logged, and a long list collapses behind "show more".
// Statuses, types, claim text, file names and logins are untrusted text, shown through PlainText.

import { useState } from "react";
import { REALITY_DUPLICATE_NOTE, REALITY_LOGINS_PERSONAL, REALITY_PHONE_POLICY, REALITY_STALENESS_NOTE, REALITY_STATEMENT } from "../fixedTexts";
import {
  IMPORT_FILES_CUT_SHORT,
  IMPORT_OTHER_VALUE_CUT,
  REALITY_CHECKING,
  REALITY_CHECK_BUTTON,
  REALITY_CHOOSE_HEADING,
  REALITY_CLAIM_CONTROLS,
  REALITY_CLAIM_DOC_ID,
  REALITY_CLAIM_FILE,
  REALITY_CLAIM_HEADING,
  REALITY_CLAIM_LINES,
  REALITY_CLAIM_MAPPED,
  REALITY_CLAIM_SCOPE_WORDS,
  REALITY_CLAIMS_NOT_LISTED,
  REALITY_CLAIMS_TOO_LONG,
  REALITY_CONFIRM_LABEL,
  REALITY_DATE_LABEL,
  REALITY_DISAGREE_TEXT,
  REALITY_EXPORT_HEADING,
  REALITY_HASH_MISMATCH_TEXT,
  REALITY_INSPECTING,
  REALITY_LABEL_CLAIM_CHECKED,
  REALITY_LABEL_EXPORT_DATE,
  REALITY_LABEL_LOGINS_HEADING,
  REALITY_LABEL_PHONE_TYPES_NAMED,
  REALITY_LABEL_SHA256_COUNTED,
  REALITY_LABEL_SHA256_READ,
  REALITY_LABEL_SKIPPED,
  REALITY_LABEL_TYPES_NOT_PRESENT,
  REALITY_MFA_HEADING,
  REALITY_NO,
  REALITY_NO_CLAIMS,
  REALITY_NO_CSV_FILES,
  REALITY_NONE,
  REALITY_NOT_CHECKED,
  REALITY_PHONE_HEADING,
  REALITY_READING_CLAIMS,
  REALITY_READING_FILES,
  REALITY_RESULT_HEADING,
  REALITY_SELECTED,
  REALITY_STATUS_HEADING,
  REALITY_TITLE,
  REALITY_UNKNOWN_FINDING_TEXT,
  REALITY_USE_CLAIM,
  REALITY_USE_EXPORT,
  REALITY_VALUE_COUNT,
  REALITY_YES,
} from "../ledgerText";
import {
  checkCountRows,
  countText,
  inspectCountRows,
  isChoosable,
  isChosen,
  isClaimChosen,
  readResult,
  realityProblem,
  realityProblemText,
  type CheckReport,
  type ChoiceKind,
  type ClaimCandidate,
  type ExportInspection,
  type InputChange,
  type RealityState,
  type ValueCount,
} from "../reality";
import { LongText } from "./LongText";
import { PlainText } from "./PlainText";
import { Refusal } from "./Refusal";
import { Statement } from "./Statement";

export interface RealityScreenProps {
  state: RealityState;
  /** False while the sidecar is not healthy. */
  enabled: boolean;
  /** The position in the listing. */
  onChooseFile: (index: number) => void;
  onInputChange: (change: InputChange) => void;
  /** Sends the check (nothing is written). */
  onCheck: () => void;
}

const BUTTON = "border border-border px-3 py-1 disabled:border-dashed";
// A native input takes its own colours unless they are set (Vercel: explicit colours).
const FIELD = "border border-border bg-surface-raised px-2 py-1 text-text";

/** A login list longer than this many entries starts collapsed behind "show more". */
export const LOGINS_SHOWN = 10;

const ERROR_BLOCK = "alibi-block border-2 border-status-error px-2 py-1 font-semibold text-status-error";

function ErrorBlock({ text }: { text: string }) {
  return (
    <p role="alert" className={ERROR_BLOCK}>
      {text}
    </p>
  );
}

function CountList({ rows }: { rows: ReadonlyArray<readonly [string, string]> }) {
  return (
    <ul className="grid gap-1 tabular-nums">
      {rows.map(([label, value]) => (
        <li key={label}>
          {label}: {value}
        </li>
      ))}
    </ul>
  );
}

// ----- the export ---------------------------------------------------------------------------

function FilesBlock({ props, locked }: { props: RealityScreenProps; locked: boolean }) {
  const { state } = props;
  const files = state.files;
  switch (files.phase) {
    case "idle":
    case "loading":
      return <p role="status">{REALITY_READING_FILES}</p>;
    case "failed":
      return <Refusal failure={files.failure} />;
    case "listed":
      if (files.files.length === 0) {
        return <p role="status">{REALITY_NO_CSV_FILES}</p>;
      }
      return (
        <>
          <ul className="grid gap-1">
            {files.files.map((file, index) => (
              <li key={`${index}:${file}`} className="flex min-w-0 flex-wrap items-center gap-2">
                <PlainText text={file} identifier />
                {state.inputs.file === file ? <span className="font-semibold">{REALITY_SELECTED}</span> : null}
                <button
                  type="button"
                  className={BUTTON}
                  disabled={!props.enabled || locked}
                  onClick={() => props.onChooseFile(index)}
                >
                  {REALITY_USE_EXPORT}
                </button>
              </li>
            ))}
          </ul>
          {files.truncated ? <p>{IMPORT_FILES_CUT_SHORT}</p> : null}
        </>
      );
  }
}

function ChoiceGroup({
  heading,
  kind,
  list,
  props,
  locked,
  note,
}: {
  heading: string;
  kind: ChoiceKind;
  list: readonly ValueCount[];
  props: RealityScreenProps;
  locked: boolean;
  /** A fixed statement shown with the group, always visible (the phone row). */
  note?: string;
}) {
  return (
    <fieldset className="grid min-w-0 gap-2 border border-border p-2">
      <legend className="px-1 font-semibold">{heading}</legend>
      {note === undefined ? null : <Statement>{note}</Statement>}
      <ul className="grid gap-1">
        {list.map((entry, index) => (
          <li key={`${index}:${entry.value}`}>
            <label className="flex min-w-0 flex-wrap items-center gap-2">
              <input
                type="checkbox"
                name={`reality-${kind}-${index}`}
                autoComplete="off"
                disabled={!props.enabled || locked || !isChoosable(entry)}
                checked={isChosen(props.state, kind, index)}
                onChange={() => props.onInputChange({ kind, index })}
              />
              <PlainText text={entry.value} />
              <span className="tabular-nums">
                {REALITY_VALUE_COUNT}: {countText(entry.count)}
              </span>
              {entry.truncated ? <span>({IMPORT_OTHER_VALUE_CUT})</span> : null}
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}

function Inspected({ report, props, locked }: { report: ExportInspection; props: RealityScreenProps; locked: boolean }) {
  return (
    <div className="grid min-w-0 gap-3" data-report="reality-inspect">
      <h3 className="text-lg font-semibold">{REALITY_EXPORT_HEADING}</h3>
      <p className="min-w-0 break-words">
        {REALITY_LABEL_SHA256_READ}: <PlainText text={report.sha256} identifier />
      </p>
      <CountList rows={inspectCountRows(report)} />
      <ChoiceGroup
        heading={REALITY_MFA_HEADING}
        kind="mfa"
        list={report.authenticator_types}
        props={props}
        locked={locked}
      />
      <ChoiceGroup
        heading={REALITY_PHONE_HEADING}
        kind="phone"
        list={report.authenticator_types}
        props={props}
        locked={locked}
        note={REALITY_PHONE_POLICY}
      />
      <ChoiceGroup
        heading={REALITY_STATUS_HEADING}
        kind="status"
        list={report.statuses}
        props={props}
        locked={locked}
      />
    </div>
  );
}

function InspectBlock({ props, locked }: { props: RealityScreenProps; locked: boolean }) {
  const inspect = props.state.inspect;
  switch (inspect.phase) {
    case "none":
      return null;
    case "loading":
      return <p role="status">{REALITY_INSPECTING}</p>;
    case "failed":
      return <Refusal failure={inspect.failure} />;
    case "inspected":
      return <Inspected report={inspect.report} props={props} locked={locked} />;
  }
}

// ----- the claim ----------------------------------------------------------------------------

function ClaimView({ claim }: { claim: ClaimCandidate }) {
  return (
    <div className="grid min-w-0 gap-1">
      <p className="min-w-0 break-words">
        {REALITY_CLAIM_FILE}: <PlainText text={claim.path} identifier />
      </p>
      <p className="min-w-0 break-words">
        {REALITY_CLAIM_DOC_ID}: <PlainText text={claim.doc_id} identifier />
      </p>
      <p className="tabular-nums">
        {REALITY_CLAIM_LINES}: {countText(claim.start_line)} {"-"} {countText(claim.end_line)}
      </p>
      <p className="min-w-0 break-words">
        <LongText text={claim.quote} />
      </p>
      <p className="min-w-0 break-words">
        {REALITY_CLAIM_SCOPE_WORDS}:{" "}
        {claim.qualifier_words.length === 0 ? (
          REALITY_NONE
        ) : (
          claim.qualifier_words.map((word, index) => (
            <span key={`${index}:${word}`}>
              {index === 0 ? "" : ", "}
              <PlainText text={word} />
            </span>
          ))
        )}
      </p>
      <p>
        {REALITY_CLAIM_MAPPED}: {claim.mapped_to_authentication_control ? REALITY_YES : REALITY_NO}
      </p>
      <p className="min-w-0 break-words">
        {REALITY_CLAIM_CONTROLS}:{" "}
        {claim.matched_control_ids.length === 0 ? (
          REALITY_NONE
        ) : (
          claim.matched_control_ids.map((id, index) => (
            <span key={`${index}:${id}`}>
              {index === 0 ? "" : ", "}
              <PlainText text={id} identifier />
            </span>
          ))
        )}
      </p>
    </div>
  );
}

function ClaimsBlock({ props, locked }: { props: RealityScreenProps; locked: boolean }) {
  const claims = props.state.claims;
  switch (claims.phase) {
    case "idle":
    case "loading":
      return <p role="status">{REALITY_READING_CLAIMS}</p>;
    case "failed":
      return <Refusal failure={claims.failure} />;
    case "listed":
      if (claims.report.candidates.length === 0) {
        return <p role="status">{REALITY_NO_CLAIMS}</p>;
      }
      return (
        <div className="grid min-w-0 gap-3">
          <ul className="grid gap-3">
            {claims.report.candidates.map((claim, index) => (
              <li key={`${index}:${claim.path}:${claim.start_line}`} className="alibi-block grid min-w-0 gap-2 border border-border p-2">
                <ClaimView claim={claim} />
                <div className="flex flex-wrap items-center gap-2">
                  {isClaimChosen(props.state, index) ? <span className="font-semibold">{REALITY_SELECTED}</span> : null}
                  <button
                    type="button"
                    className={BUTTON}
                    disabled={!props.enabled || locked}
                    onClick={() => props.onInputChange({ kind: "claim", index })}
                  >
                    {REALITY_USE_CLAIM}
                  </button>
                </div>
              </li>
            ))}
          </ul>
          <CountList
            rows={[
              [REALITY_CLAIMS_NOT_LISTED, countText(claims.report.candidates_not_listed)],
              [REALITY_CLAIMS_TOO_LONG, countText(claims.report.too_long)],
            ]}
          />
        </div>
      );
  }
}

// ----- the result ---------------------------------------------------------------------------

function LoginList({ logins }: { logins: CheckReport["exceptions"] }) {
  const [open, setOpen] = useState(false);
  const long = logins.length > LOGINS_SHOWN;
  const shown = long && !open ? logins.slice(0, LOGINS_SHOWN) : logins;
  return (
    <div className="grid min-w-0 gap-2" data-logins="list">
      <ul className="grid gap-1">
        {shown.map((entry, index) => (
          <li key={`${index}:${entry.login}`} className="min-w-0 break-words">
            <PlainText text={entry.login} />
            {entry.truncated ? <span> ({IMPORT_OTHER_VALUE_CUT})</span> : null}
          </li>
        ))}
      </ul>
      {long ? (
        <button
          type="button"
          className="w-fit border border-border px-2 py-1 text-sm"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Show less" : "Show more"}
        </button>
      ) : null}
    </div>
  );
}

function Result({ report, inspectSha256 }: { report: CheckReport; inspectSha256: string }) {
  const reading = readResult(report, inspectSha256);
  switch (reading.kind) {
    case "disagree":
      return <ErrorBlock text={REALITY_DISAGREE_TEXT} />;
    case "unknown_finding":
      return <ErrorBlock text={REALITY_UNKNOWN_FINDING_TEXT} />;
    case "hash_mismatch":
      return (
        <div className="grid min-w-0 gap-2">
          <ErrorBlock text={REALITY_HASH_MISMATCH_TEXT} />
          <p className="min-w-0 break-words">
            {REALITY_LABEL_SHA256_READ}: <PlainText text={inspectSha256} identifier />
          </p>
          <p className="min-w-0 break-words">
            {REALITY_LABEL_SHA256_COUNTED}: <PlainText text={report.sha256} identifier />
          </p>
        </div>
      );
    case "sentence":
      return (
        <div className="grid min-w-0 gap-3" data-report="reality-check">
          <h3 className="text-lg font-semibold">{REALITY_RESULT_HEADING}</h3>
          <Statement>{reading.text}</Statement>
          <Statement>{REALITY_STATEMENT}</Statement>
          <Statement>{REALITY_STALENESS_NOTE}</Statement>
          <Statement>{REALITY_DUPLICATE_NOTE}</Statement>
          <p className="min-w-0 break-words">
            {REALITY_LABEL_SHA256_READ}: <PlainText text={inspectSha256} identifier />
          </p>
          <p className="min-w-0 break-words">
            {REALITY_LABEL_SHA256_COUNTED}: <PlainText text={report.sha256} identifier />
          </p>
          <p>
            {REALITY_LABEL_EXPORT_DATE}: <PlainText text={report.exported_at} identifier />
          </p>
          <div className="grid min-w-0 gap-1">
            <p className="font-semibold">{REALITY_LABEL_CLAIM_CHECKED}:</p>
            <ClaimView claim={report.claim} />
          </div>
          <CountList rows={checkCountRows(report)} />
          <p>
            {REALITY_LABEL_PHONE_TYPES_NAMED}: {report.phone_types_named ? REALITY_YES : REALITY_NO}
          </p>
          <div className="min-w-0">
            <p className="font-semibold">{REALITY_LABEL_SKIPPED}:</p>
            {report.skipped.length === 0 ? (
              <p>{REALITY_NONE}</p>
            ) : (
              <ul className="grid gap-1 tabular-nums">
                {report.skipped.map((entry, index) => (
                  <li key={`${index}:${entry.value}`} className="min-w-0 break-words">
                    <PlainText text={entry.value} /> ({REALITY_VALUE_COUNT}: {countText(entry.count)})
                    {entry.truncated ? <span> ({IMPORT_OTHER_VALUE_CUT})</span> : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="min-w-0">
            <p className="font-semibold">{REALITY_LABEL_TYPES_NOT_PRESENT}:</p>
            {report.mfa_types_not_present.length === 0 ? (
              <p>{REALITY_NONE}</p>
            ) : (
              <ul className="grid gap-1">
                {report.mfa_types_not_present.map((name, index) => (
                  <li key={`${index}:${name}`} className="min-w-0 break-words">
                    <PlainText text={name} />
                  </li>
                ))}
              </ul>
            )}
          </div>
          {report.exceptions.length === 0 ? null : (
            <div className="grid min-w-0 gap-2">
              <h3 className="text-lg font-semibold">{REALITY_LABEL_LOGINS_HEADING}</h3>
              <Statement>{REALITY_LOGINS_PERSONAL}</Statement>
              <LoginList logins={report.exceptions} />
            </div>
          )}
        </div>
      );
  }
}

function CheckBlock({ props }: { props: RealityScreenProps }) {
  const check = props.state.check;
  switch (check.phase) {
    case "none":
      return <p role="status">{REALITY_NOT_CHECKED}</p>;
    case "loading":
      return <p role="status">{REALITY_CHECKING}</p>;
    case "failed":
      return <Refusal failure={check.failure} />;
    case "checked":
      return <Result report={check.report} inspectSha256={check.inspectSha256} />;
  }
}

export function RealityScreen(props: RealityScreenProps) {
  const { state, enabled } = props;
  const busy = state.check.phase === "loading" || state.inspect.phase === "loading";
  const problem = realityProblem(state);
  return (
    <section aria-labelledby="reality-title" className="grid gap-4">
      <h2 id="reality-title" className="text-xl font-semibold">
        {REALITY_TITLE}
      </h2>
      <h3 className="text-lg font-semibold">{REALITY_CHOOSE_HEADING}</h3>
      <FilesBlock props={props} locked={busy} />
      <InspectBlock props={props} locked={busy} />
      <h3 className="text-lg font-semibold">{REALITY_CLAIM_HEADING}</h3>
      <ClaimsBlock props={props} locked={busy} />
      <div className="grid gap-1">
        <label htmlFor="reality-date" className="font-semibold">
          {REALITY_DATE_LABEL}
        </label>
        <input
          id="reality-date"
          name="reality-date"
          type="text"
          inputMode="numeric"
          autoComplete="off"
          spellCheck={false}
          className={`${FIELD} w-40 tabular-nums`}
          disabled={!enabled || busy}
          value={state.inputs.exportedAt}
          onChange={(event) => props.onInputChange({ kind: "date", text: event.target.value })}
        />
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <input
          id="reality-confirm"
          name="reality-confirm"
          type="checkbox"
          autoComplete="off"
          disabled={!enabled || busy}
          checked={state.inputs.confirmed}
          onChange={(event) => props.onInputChange({ kind: "confirm", confirmed: event.target.checked })}
        />
        <label htmlFor="reality-confirm">{REALITY_CONFIRM_LABEL}</label>
      </div>
      <div className="grid gap-2">
        {problem === null ? null : <p role="status">{realityProblemText(problem)}</p>}
        <div>
          <button type="button" className={BUTTON} disabled={!enabled || busy || problem !== null} onClick={props.onCheck}>
            {REALITY_CHECK_BUTTON}
          </button>
        </div>
      </div>
      <CheckBlock props={props} />
    </section>
  );
}
