/**
 * Pure helpers of the import assistant: the profile form, the user's corrections of a preview and
 * what they mean for the commit. The engine classifies; nothing here recomputes its answer.
 */
import type { RuleInput } from "../month/monthApi";
import type {
  ColumnMap,
  DecimalSep,
  Delimiter,
  Detected,
  Encoding,
  Override,
  PreviewLine,
  Profile,
  ProfileInput,
} from "./importApi";

// --- profile form ------------------------------------------------------------------------------

/** Date formats the engine accepts (day, month, year directives with literal separators). */
export const DATE_FORMATS = ["%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"];

/** The profile as edited: text fields, `amountMode` decides which amount columns count. */
export interface ProfileForm {
  bankName: string;
  amountMode: "single" | "split";
  date: string;
  text: string;
  amount: string;
  debit: string;
  credit: string;
  counterparty: string;
  delimiter: Delimiter;
  encoding: Encoding;
  dateFormat: string;
  decimalSep: DecimalSep;
  skipRows: string;
}

export const emptyForm = (): ProfileForm => ({
  bankName: "",
  amountMode: "single",
  date: "",
  text: "",
  amount: "",
  debit: "",
  credit: "",
  counterparty: "",
  delimiter: "auto",
  encoding: "auto",
  dateFormat: DATE_FORMATS[0] ?? "%d.%m.%Y",
  decimalSep: ",",
  skipRows: "0",
});

export function formFromProfile(profile: Profile): ProfileForm {
  const map = profile.column_map;
  return {
    bankName: profile.bank_name,
    amountMode: map.amount === null ? "split" : "single",
    date: map.date,
    text: map.text,
    amount: map.amount ?? "",
    debit: map.debit ?? "",
    credit: map.credit ?? "",
    counterparty: map.counterparty ?? "",
    delimiter: profile.delimiter,
    encoding: profile.encoding,
    dateFormat: profile.date_format,
    decimalSep: profile.decimal_sep,
    skipRows: String(profile.skip_rows),
  };
}

const KEYWORDS: Record<
  "date" | "text" | "amount" | "debit" | "credit" | "counterparty",
  string[]
> = {
  date: ["buchungstag", "datum", "date", "valuta"],
  text: ["verwendungszweck", "buchungstext", "zweck", "text", "reference"],
  amount: ["betrag", "amount"],
  debit: ["soll", "debit"],
  credit: ["haben", "credit"],
  counterparty: ["partner", "empf", "auftraggeber", "counterparty", "name"],
};

/** A first proposal for the column choices from the header names; the user reviews it. */
export function guessColumns(header: string[]): Partial<ProfileForm> {
  const find = (role: keyof typeof KEYWORDS) =>
    header.find((name) =>
      KEYWORDS[role].some((word) => name.toLowerCase().includes(word)),
    ) ?? "";
  const amount = find("amount");
  const debit = find("debit");
  const credit = find("credit");
  return {
    date: find("date"),
    text: find("text"),
    counterparty: find("counterparty"),
    amount,
    debit,
    credit,
    amountMode:
      amount === "" && debit !== "" && credit !== "" ? "split" : "single",
  };
}

export type Problem =
  | "bank"
  | "date"
  | "text"
  | "amount"
  | "debit"
  | "credit"
  | "dateFormat"
  | "skipRows";

const skipRowsValue = (form: ProfileForm): number | null => {
  const value = Number(form.skipRows);
  return form.skipRows.trim() !== "" &&
    Number.isInteger(value) &&
    value >= 0 &&
    value <= 50
    ? value
    : null;
};

/** The fields that block saving the profile; the engine validates the rest and names the column. */
export function problems(form: ProfileForm): Problem[] {
  const found: Problem[] = [];
  if (!form.bankName.trim()) found.push("bank");
  if (!form.date) found.push("date");
  if (!form.text) found.push("text");
  if (form.amountMode === "single") {
    if (!form.amount) found.push("amount");
  } else {
    if (!form.debit) found.push("debit");
    if (!form.credit) found.push("credit");
  }
  if (!form.dateFormat.trim()) found.push("dateFormat");
  if (skipRowsValue(form) === null) found.push("skipRows");
  return found;
}

/** The API body for a valid form, else null. */
export function toProfileInput(form: ProfileForm): ProfileInput | null {
  const skip = skipRowsValue(form);
  if (problems(form).length > 0 || skip === null) return null;
  const single = form.amountMode === "single";
  const column_map: ColumnMap = {
    date: form.date,
    text: form.text,
    amount: single ? form.amount : null,
    debit: single ? null : form.debit,
    credit: single ? null : form.credit,
    counterparty: form.counterparty || null,
  };
  return {
    bank_name: form.bankName.trim(),
    column_map,
    delimiter: form.delimiter,
    encoding: form.encoding,
    date_format: form.dateFormat.trim(),
    decimal_sep: form.decimalSep,
    skip_rows: skip,
  };
}

/** Whether the form differs from the stored profile (then it is saved before the preview). */
export function isChanged(stored: Profile, form: ProfileForm): boolean {
  const input = toProfileInput(form);
  return (
    input === null ||
    JSON.stringify(input) !==
      JSON.stringify({
        bank_name: stored.bank_name,
        column_map: stored.column_map,
        delimiter: stored.delimiter,
        encoding: stored.encoding,
        date_format: stored.date_format,
        decimal_sep: stored.decimal_sep,
        skip_rows: stored.skip_rows,
      })
  );
}

// UTF-8 text read as Windows-1252 ("Ã¤" for ä, "â‚¬" for €), or a replacement character.
const GARBLED = /�|Ã[¤¶¼Ÿ„–œ]|â‚¬/;

/** True when the shown header or rows look like a wrong encoding (umlauts, the euro sign). */
export function looksGarbled(detected: Detected): boolean {
  return GARBLED.test([...detected.header, ...detected.rows.flat()].join(" "));
}

// --- corrections of a preview ------------------------------------------------------------------

/**
 * What the user changed on one line, only where it differs from the engine's answer: `skip`
 * (leave a line out / import a suspect), `categoryId`, `planned` (a candidate id, or null =
 * no match).
 */
export interface LineEdit {
  skip?: boolean;
  categoryId?: number;
  planned?: number | null;
}
export type Edits = Record<number, LineEdit>;

/** The engine's default: duplicates and suspects stay out, everything else is imported. */
const skippedByDefault = (line: PreviewLine): boolean =>
  line.kind === "duplicate" || line.kind === "duplicate_suspect";

/** Whether the commit writes the line. A duplicate is never written (the engine refuses it). */
export function willWrite(line: PreviewLine, edit?: LineEdit): boolean {
  if (line.kind === "duplicate") return false;
  return !(edit?.skip ?? skippedByDefault(line));
}

export const effectiveCategory = (line: PreviewLine, edit?: LineEdit) =>
  edit?.categoryId ?? line.category_id;

export const effectivePlanned = (line: PreviewLine, edit?: LineEdit) =>
  edit !== undefined && edit.planned !== undefined
    ? edit.planned
    : line.planned_id;

/**
 * The edits after one change of a line. A change back to the engine's answer removes the edit, so
 * an untouched line sends no override.
 */
export function withEdit(
  edits: Edits,
  line: PreviewLine,
  change: {
    skip?: boolean;
    categoryId?: number | null;
    planned?: number | null;
  },
): Edits {
  const next: LineEdit = { ...edits[line.line] };
  if (change.skip !== undefined) {
    if (change.skip === skippedByDefault(line)) delete next.skip;
    else next.skip = change.skip;
  }
  if (change.categoryId !== undefined) {
    if (change.categoryId === null || change.categoryId === line.category_id) {
      delete next.categoryId;
    } else next.categoryId = change.categoryId;
  }
  if (change.planned !== undefined) {
    if (change.planned === line.planned_id) delete next.planned;
    else next.planned = change.planned;
  }
  const rest = { ...edits };
  if (Object.keys(next).length === 0) delete rest[line.line];
  else rest[line.line] = next;
  return rest;
}

/**
 * The edits carried over to a new preview of the same file (after a rule was saved): what the new
 * answer now says itself is dropped, and a match the line cannot take any more is dropped too.
 */
export function reconcileEdits(lines: PreviewLine[], edits: Edits): Edits {
  let next: Edits = {};
  for (const line of lines) {
    const edit = edits[line.line];
    if (edit === undefined) continue;
    const planned =
      edit.planned === undefined ||
      (edit.planned !== null && !line.candidates.includes(edit.planned))
        ? undefined
        : edit.planned;
    next = withEdit(next, line, {
      skip: edit.skip,
      categoryId: edit.categoryId,
      planned,
    });
  }
  return next;
}

/** The commit's `overrides`: exactly the edits, in line order. */
export function buildOverrides(lines: PreviewLine[], edits: Edits): Override[] {
  const overrides: Override[] = [];
  for (const line of lines) {
    const edit = edits[line.line];
    if (edit === undefined) continue;
    const override: Override = { line: line.line };
    if (edit.skip !== undefined) override.skip = edit.skip;
    if (edit.categoryId !== undefined) override.category_id = edit.categoryId;
    if (edit.planned !== undefined) override.planned_id = edit.planned;
    overrides.push(override);
  }
  return overrides;
}

/** Planned items claimed by the written lines other than `except` (the engine takes each once). */
export function claimedPlanned(
  lines: PreviewLine[],
  edits: Edits,
  except: number,
): Set<number> {
  const claimed = new Set<number>();
  for (const line of lines) {
    if (line.line === except || !willWrite(line, edits[line.line])) continue;
    const planned = effectivePlanned(line, edits[line.line]);
    if (planned !== null) claimed.add(planned);
  }
  return claimed;
}

export interface LiveCounts {
  /** Lines the commit would write. */
  write: number;
  /** … of them booked as new entries / replacing a planned item. */
  fresh: number;
  matched: number;
  /** Readable lines left out (duplicates are not counted here). */
  skipped: number;
  /** Written lines that have no category yet. */
  uncategorized: number;
}

export function liveCounts(lines: PreviewLine[], edits: Edits): LiveCounts {
  const counts: LiveCounts = {
    write: 0,
    fresh: 0,
    matched: 0,
    skipped: 0,
    uncategorized: 0,
  };
  for (const line of lines) {
    const edit = edits[line.line];
    if (!willWrite(line, edit)) {
      if (line.kind !== "duplicate") counts.skipped += 1;
      continue;
    }
    counts.write += 1;
    if (effectivePlanned(line, edit) === null) counts.fresh += 1;
    else counts.matched += 1;
    if (effectiveCategory(line, edit) === null) counts.uncategorized += 1;
  }
  return counts;
}

/** The rule offered for a corrected category: contains <text or counterparty> → the category. */
export function ruleDraft(
  line: PreviewLine,
  categoryId: number,
): RuleInput | null {
  const pattern = (line.text.trim() || (line.counterparty ?? "").trim()).slice(
    0,
    200,
  );
  if (!pattern) return null;
  return {
    match_kind: "contains",
    pattern,
    category_id: categoryId,
    counterparty: line.counterparty,
    priority: 0,
  };
}
