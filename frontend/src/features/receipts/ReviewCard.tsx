import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type ChangeEvent, type FormEvent } from "react";

import {
  centsToInput,
  parseEuroInput,
  type Account,
  type Category,
} from "../accounts/accountsApi";
import { Button, Card, Field } from "../../ui";
import { REASON_LABELS } from "./labels";
import type { BookInput, Receipt } from "./receiptsApi";

interface Props {
  receipt: Receipt;
  accounts: Account[];
  categories: Category[];
  today: string;
  onBook: (receipt: Receipt, input: BookInput) => Promise<boolean>;
  onDiscard: (receipt: Receipt) => void;
}

/** The correction form's values, prefilled from what the agent read. */
export interface CorrectionDraft {
  date: string;
  amount: string;
  merchant: string;
  accountId: string;
  categoryId: string;
}

export function initialDraft(
  receipt: Receipt,
  categories: Category[],
  today: string,
  locale: string,
): CorrectionDraft {
  const read = receipt.extracted;
  const suggestion = (read.category_suggestion ?? "").trim().toLowerCase();
  const category = categories.find(
    (c) => c.kind === "expense" && c.name.toLowerCase() === suggestion,
  );
  return {
    date: read.date ?? today,
    amount:
      typeof read.amount_cents === "number" && read.amount_cents > 0
        ? centsToInput(read.amount_cents, locale)
        : "",
    merchant: read.merchant ?? "",
    accountId: "",
    categoryId: category ? String(category.id) : "",
  };
}

/**
 * The correction form as an API body, or the label of the first field that is missing or wrong
 * (the API is not called then).
 */
export function toBookInput(
  draft: CorrectionDraft,
):
  | { input: BookInput }
  | { missing: "date" | "amount" | "merchant" | "account" } {
  const cents = parseEuroInput(draft.amount);
  if (!draft.date) return { missing: "date" };
  if (cents === null || cents <= 0) return { missing: "amount" };
  if (!draft.merchant.trim()) return { missing: "merchant" };
  if (!draft.accountId) return { missing: "account" };
  return {
    input: {
      date: draft.date,
      amount_cents: cents,
      merchant: draft.merchant.trim(),
      account_id: Number(draft.accountId),
      category_id: draft.categoryId ? Number(draft.categoryId) : null,
    },
  };
}

/**
 * One entry of the review list (UC-ZF-013 step 7): the photo, why it is here, and a form with
 * what the agent read. "Book" saves the corrected values as a booking; "Discard" removes the
 * receipt and its photo.
 */
export function ReviewCard({
  receipt,
  accounts,
  categories,
  today,
  onBook,
  onDiscard,
}: Props) {
  const { t, i18n } = useLingui();
  const [draft, setDraft] = useState(() =>
    initialDraft(receipt, categories, today, i18n.locale),
  );
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const expenseCategories = categories.filter((c) => c.kind === "expense");
  const id = `receipt-${receipt.id}`;
  const read = receipt.extracted;
  const receiptId = receipt.id;
  const percent =
    typeof read.confidence === "number"
      ? Math.round(read.confidence * 100)
      : null;

  const missingText = {
    date: t`Enter the date.`,
    amount: t`Enter the total as an amount greater than 0.`,
    merchant: t`Enter the merchant.`,
    account: t`Choose the account.`,
  };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const result = toBookInput(draft);
    if ("missing" in result) {
      setProblem(missingText[result.missing]);
      return;
    }
    setProblem(null);
    setBusy(true);
    const ok = await onBook(receipt, result.input);
    if (!ok) setBusy(false);
  }

  const set =
    (field: keyof CorrectionDraft) =>
    (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setDraft({ ...draft, [field]: event.target.value });

  return (
    <Card as="article" className="rcp-card" aria-labelledby={`${id}-title`}>
      <a
        className="rcp-photo"
        href={receipt.photo_url}
        target="_blank"
        rel="noreferrer"
      >
        <img src={receipt.photo_url} alt={t`Receipt photo ${receiptId}`} />
      </a>
      <div className="rcp-body">
        <h3 id={`${id}-title`}>
          {receipt.status === "duplicate" ? (
            <Trans>Possible duplicate</Trans>
          ) : receipt.reason ? (
            i18n._(REASON_LABELS[receipt.reason])
          ) : (
            <Trans>To check</Trans>
          )}
        </h3>
        <p className="rcp-read">
          <Trans>Read by the agent:</Trans>{" "}
          {[
            read.date,
            typeof read.amount_cents === "number"
              ? `${centsToInput(read.amount_cents, i18n.locale)} ${read.currency ?? ""}`.trim()
              : null,
            read.merchant,
            percent === null ? null : t`confidence ${percent} %`,
            read.hints ? `“${read.hints}”` : null,
          ]
            .filter(Boolean)
            .join(" · ") || "—"}
        </p>
        <form className="rcp-form" onSubmit={submit} noValidate>
          <div className="ui-form-grid">
            <Field htmlFor={`${id}-date`} label={<Trans>Date</Trans>}>
              <input
                id={`${id}-date`}
                type="date"
                value={draft.date}
                onChange={set("date")}
              />
            </Field>
            <Field htmlFor={`${id}-amount`} label={<Trans>Total (€)</Trans>}>
              <input
                id={`${id}-amount`}
                inputMode="decimal"
                value={draft.amount}
                onChange={set("amount")}
              />
            </Field>
            <Field htmlFor={`${id}-merchant`} label={<Trans>Merchant</Trans>}>
              <input
                id={`${id}-merchant`}
                value={draft.merchant}
                maxLength={200}
                onChange={set("merchant")}
              />
            </Field>
            <Field htmlFor={`${id}-account`} label={<Trans>Account</Trans>}>
              <select
                id={`${id}-account`}
                value={draft.accountId}
                onChange={set("accountId")}
              >
                <option value="">{t`Choose an account…`}</option>
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field htmlFor={`${id}-category`} label={<Trans>Category</Trans>}>
              <select
                id={`${id}-category`}
                value={draft.categoryId}
                onChange={set("categoryId")}
              >
                <option value="">{t`By the rules`}</option>
                {expenseCategories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {problem && (
            <p className="error" role="alert">
              {problem}
            </p>
          )}
          <div className="ui-actions">
            <Button variant="primary" type="submit" disabled={busy}>
              <Trans>Book</Trans>
            </Button>
            <Button
              variant="danger"
              type="button"
              disabled={busy}
              onClick={() => onDiscard(receipt)}
            >
              <Trans>Discard</Trans>
            </Button>
          </div>
        </form>
      </div>
    </Card>
  );
}
