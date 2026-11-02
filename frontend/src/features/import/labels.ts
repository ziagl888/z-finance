import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";

import type { DecimalSep, Delimiter, Encoding, LineKind } from "./importApi";

export const ENCODINGS: Encoding[] = ["auto", "utf-8", "cp1252"];
export const DELIMITERS: Delimiter[] = ["auto", "semicolon", "comma", "tab"];

/** Display names of the stored codes; render with `i18n._(...)`. */
export const ENCODING_LABELS: Record<Encoding, MessageDescriptor> = {
  auto: msg`Detect automatically`,
  "utf-8": msg`UTF-8`,
  cp1252: msg`Windows-1252 (older Windows exports)`,
};

export const DELIMITER_LABELS: Record<Delimiter, MessageDescriptor> = {
  auto: msg`Detect automatically`,
  semicolon: msg`Semicolon ( ; )`,
  comma: msg`Comma ( , )`,
  tab: msg`Tab`,
};

export const DECIMAL_LABELS: Record<DecimalSep, MessageDescriptor> = {
  ",": msg`Comma, e.g. 1.234,56`,
  ".": msg`Point, e.g. 1,234.56`,
};

export const KIND_LABELS: Record<LineKind, MessageDescriptor> = {
  new: msg`New`,
  duplicate: msg`Duplicate`,
  match: msg`Matches planned item`,
  duplicate_suspect: msg`Possible duplicate`,
};
