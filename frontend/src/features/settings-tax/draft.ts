/**
 * Form state of the tax settings and its conversion to the API body (task -014, UC-ZF-008).
 * Amounts are typed without a sign and parsed to integer cents without floats.
 */
import { centsToInput, parseEuroInput } from "../accounts/accountsApi";
import type { TaxInput, TaxSettings } from "./taxApi";

export interface TaxDraft {
  accountId: string;
  est: string;
  svs: string;
  estDates: string[];
  svsDates: string[];
}

export type Invalid = "est" | "svs" | "account" | "estDates" | "svsDates";

/** The form filled with the stored settings; ``fallbackAccountId`` is offered while none is stored. */
export function draftFrom(
  settings: TaxSettings,
  locale: string,
  fallbackAccountId: string,
): TaxDraft {
  const cents = (value: number | null) =>
    value === null ? "" : centsToInput(value, locale);
  return {
    accountId:
      settings.business_account_id === null
        ? fallbackAccountId
        : String(settings.business_account_id),
    est: cents(settings.est_vz_quarter_cents),
    svs: cents(settings.svs_quarter_cents),
    estDates: settings.est_due_dates,
    svsDates: settings.svs_due_dates,
  };
}

/** An empty field is "not entered" (null); anything else must be an amount greater than 0. */
function amount(text: string): number | null | "invalid" {
  if (!text.trim()) return null;
  const cents = parseEuroInput(text);
  return cents === null || cents <= 0 ? "invalid" : cents;
}

const distinct = (dates: string[]) =>
  dates.length >= 1 && new Set(dates).size === dates.length;

/** The API body of a draft, or the first invalid field. */
export function toInput(
  draft: TaxDraft,
): { input: TaxInput } | { invalid: Invalid } {
  const est = amount(draft.est);
  if (est === "invalid") return { invalid: "est" };
  const svs = amount(draft.svs);
  if (svs === "invalid") return { invalid: "svs" };
  if ((est !== null || svs !== null) && !draft.accountId) {
    return { invalid: "account" };
  }
  if (!distinct(draft.estDates)) return { invalid: "estDates" };
  if (!distinct(draft.svsDates)) return { invalid: "svsDates" };
  return {
    input: {
      business_account_id: draft.accountId ? Number(draft.accountId) : null,
      est_vz_quarter_cents: est,
      svs_quarter_cents: svs,
      est_due_dates: draft.estDates,
      svs_due_dates: draft.svsDates,
    },
  };
}
