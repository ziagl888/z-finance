/**
 * Form state of a debt and its conversion to the API body (task -013, UC-ZF-010 step 1). Amounts
 * are typed without a sign and parsed to integer cents without floats.
 */
import {
  centsToInput,
  parseEuroInput,
  type Area,
} from "../accounts/accountsApi";
import type { Debt, DebtCreateInput, DebtInterval } from "./debtsApi";

export interface DebtDraft {
  creditor: string;
  area: Area;
  total: string;
  /** Only when creating: the part of the total already paid back. */
  repaid: string;
  /** Fixed instalment (UC step 1) or "pay when possible" (UC 1a). */
  hasInstallment: boolean;
  installment: string;
  interval: DebtInterval;
  firstDue: string;
  payAccountId: string;
  interestRate: string;
  contractEnd: string;
  targetEnd: string;
}

export type Invalid =
  | "creditor"
  | "total"
  | "repaid"
  | "installment"
  | "firstDue"
  | "payAccount"
  | "interestRate";

export function emptyDraft(payAccountId: string): DebtDraft {
  return {
    creditor: "",
    area: "private",
    total: "",
    repaid: "",
    hasInstallment: true,
    installment: "",
    interval: "monthly",
    firstDue: "",
    payAccountId,
    interestRate: "",
    contractEnd: "",
    targetEnd: "",
  };
}

/** The form filled with a stored debt (for editing). */
export function draftFrom(debt: Debt, locale: string): DebtDraft {
  return {
    creditor: debt.creditor,
    area: debt.area,
    total: centsToInput(debt.total_amount_cents, locale),
    repaid: "",
    hasInstallment: debt.installment_cents !== null,
    installment:
      debt.installment_cents === null
        ? ""
        : centsToInput(debt.installment_cents, locale),
    interval: debt.interval ?? "monthly",
    firstDue: debt.first_due ?? "",
    payAccountId:
      debt.pay_account_id === null ? "" : String(debt.pay_account_id),
    interestRate: debt.interest_rate_pct ?? "",
    contractEnd: debt.contract_end ?? "",
    targetEnd: debt.target_end ?? "",
  };
}

/** A positive amount in cents, or null. */
function positive(text: string): number | null {
  const cents = parseEuroInput(text);
  return cents !== null && cents > 0 ? cents : null;
}

const RATE = /^\d{1,3}(?:[.,]\d{1,4})?$/;

/**
 * The API body, or the first invalid field. The repaid amount (empty = 0) must stay below the
 * total; an instalment needs amount, first due date and paying account. Without an instalment
 * the target end is kept, with one it is dropped (it only drives the rate of UC 1a).
 */
export function toInput(
  draft: DebtDraft,
): { input: DebtCreateInput } | { invalid: Invalid } {
  const creditor = draft.creditor.trim();
  if (!creditor) return { invalid: "creditor" };
  const total = positive(draft.total);
  if (total === null) return { invalid: "total" };
  const repaid = draft.repaid.trim() === "" ? 0 : parseEuroInput(draft.repaid);
  if (repaid === null || repaid < 0 || repaid >= total) {
    return { invalid: "repaid" };
  }
  const rate = draft.interestRate.trim();
  if (rate !== "" && !RATE.test(rate)) return { invalid: "interestRate" };
  const payAccount =
    draft.payAccountId === "" ? null : Number(draft.payAccountId);
  let installment: number | null = null;
  if (draft.hasInstallment) {
    installment = positive(draft.installment);
    if (installment === null) return { invalid: "installment" };
    if (draft.firstDue === "") return { invalid: "firstDue" };
    if (payAccount === null) return { invalid: "payAccount" };
  }
  return {
    input: {
      creditor,
      area: draft.area,
      total_amount_cents: total,
      repaid_cents: repaid,
      installment_cents: installment,
      interval: draft.hasInstallment ? draft.interval : null,
      first_due: draft.hasInstallment ? draft.firstDue : null,
      pay_account_id: payAccount,
      interest_rate_pct: rate === "" ? null : rate,
      contract_end: draft.contractEnd === "" ? null : draft.contractEnd,
      target_end:
        draft.hasInstallment || draft.targetEnd === "" ? null : draft.targetEnd,
    },
  };
}
