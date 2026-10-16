// The mapping the user confirms before a run (M6_PLAN C.4 "Mapping"). Pure.
//
// The sidecar's `suggestion` seeds the choice; the user can change the sheet and the three numbers.
// The bounds are the ones the request schema enforces (ipc/proxy.ts `mappingSchema`), so a value
// that would be refused is shown as a problem BEFORE Start, with Start disabled.

export interface MappingDraft {
  sheet: string;
  headerRow: number;
  questionCol: number;
  /** `null` means no id column. */
  idCol: number | null;
}

export const HEADER_ROW_MAX = 1_048_576;
export const COLUMN_MAX = 16_384;

export type MappingProblem =
  | "sheet_not_listed"
  | "header_row_out_of_range"
  | "question_col_out_of_range"
  | "id_col_out_of_range";

/** Seed the choice from the suggestion. With none, start on the first sheet at row 1, column 1. */
export function mappingFromSuggestion(
  suggestion: { sheet: string; header_row: number; question_col: number; id_col: number | null } | null,
  sheets: ReadonlyArray<{ name: string }>,
): MappingDraft | null {
  if (suggestion !== null) {
    return {
      sheet: suggestion.sheet,
      headerRow: suggestion.header_row,
      questionCol: suggestion.question_col,
      idCol: suggestion.id_col,
    };
  }
  const first = sheets[0];
  return first === undefined ? null : { sheet: first.name, headerRow: 1, questionCol: 1, idCol: null };
}

function inRange(value: number, max: number): boolean {
  return Number.isInteger(value) && value >= 1 && value <= max;
}

export function mappingProblems(
  mapping: MappingDraft,
  sheets: ReadonlyArray<{ name: string }>,
): readonly MappingProblem[] {
  const problems: MappingProblem[] = [];
  if (!sheets.some((sheet) => sheet.name === mapping.sheet)) {
    problems.push("sheet_not_listed");
  }
  if (!inRange(mapping.headerRow, HEADER_ROW_MAX)) {
    problems.push("header_row_out_of_range");
  }
  if (!inRange(mapping.questionCol, COLUMN_MAX)) {
    problems.push("question_col_out_of_range");
  }
  if (mapping.idCol !== null && !inRange(mapping.idCol, COLUMN_MAX)) {
    problems.push("id_col_out_of_range");
  }
  return problems;
}

export function mappingProblemMessage(problem: MappingProblem): string {
  switch (problem) {
    case "sheet_not_listed":
      return "Choose one of the sheets listed above.";
    case "header_row_out_of_range":
      return `The header row must be a whole number from 1 to ${HEADER_ROW_MAX}.`;
    case "question_col_out_of_range":
      return `The question column must be a whole number from 1 to ${COLUMN_MAX}.`;
    case "id_col_out_of_range":
      return `The id column must be empty or a whole number from 1 to ${COLUMN_MAX}.`;
  }
}

/** The request body for `questionnaire_start`'s `mapping`, or `null` while there are problems. */
export function mappingRequest(
  mapping: MappingDraft,
  sheets: ReadonlyArray<{ name: string }>,
): { sheet: string; header_row: number; question_col: number; id_col?: number } | null {
  if (mappingProblems(mapping, sheets).length > 0) {
    return null;
  }
  const base = { sheet: mapping.sheet, header_row: mapping.headerRow, question_col: mapping.questionCol };
  return mapping.idCol === null ? base : { ...base, id_col: mapping.idCol };
}
