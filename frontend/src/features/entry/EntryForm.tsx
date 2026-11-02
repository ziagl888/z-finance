import { Trans, useLingui } from "@lingui/react/macro";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { formatCents } from "../../api/client";
import type { Account, Category } from "../accounts/accountsApi";
import { CATEGORY_KIND_LABELS } from "../accounts/labels";
import {
  afterSave,
  draftFrom,
  effectiveKind,
  emptyDraft,
  isFuture,
  kindMismatch,
  magnitudeCents,
  sameText,
  signedCents,
  todayIso,
  type Draft,
  type Kind,
} from "./draft";
import {
  createTransaction,
  createTransfer,
  suggest,
  updateTransaction,
  updateTransfer,
  type Suggestion,
  type Transaction,
} from "./entryApi";
import { FOCUS_ENTRY_EVENT } from "./shortcut";
import { Button, Field } from "../../ui";

const LAST_ACCOUNT_KEY = "zfinance.entry.lastAccountId";
const SUGGEST_DELAY_MS = 150;

/** Remembered account of the last booking; storage may be unavailable (private mode). */
function rememberedAccount(): string {
  try {
    return window.localStorage.getItem(LAST_ACCOUNT_KEY) ?? "";
  } catch {
    return "";
  }
}

function rememberAccount(accountId: string): void {
  try {
    window.localStorage.setItem(LAST_ACCOUNT_KEY, accountId);
  } catch {
    // Only a convenience: without storage the form starts with the first account.
  }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

type Invalid = "amount" | "category" | "account" | null;

interface Props {
  accounts: Account[];
  /** Every account, archived ones included; an edited booking keeps its own archived account. */
  allAccounts?: Account[];
  categories: Category[];
  /** Resolves once the initial accounts fetch has completed; already-resolved once it has. A save
   * started before then (keyboard-only, typed faster than the network) awaits it instead of
   * failing on an account list that is merely not there *yet* (T-2026-CU-9130-043). */
  waitAccountsReady: () => Promise<void>;
  /** Booking opened from the list for editing, or null for new entries. */
  editing: Transaction | null;
  /** Called after every successful save (reload list and balances). */
  onSaved: () => Promise<void>;
  onCancelEdit: () => void;
}

/**
 * The quick-entry form (UC-ZF-002): income, expense or transfer. Enter saves from any field; after
 * saving, date and account stay and the amount has the focus again. Typing a known text fills
 * category and counterparty from the last booking with that text, until a category is chosen by
 * hand. A failed save keeps every value.
 */
export function EntryForm({
  accounts,
  allAccounts = accounts,
  categories,
  waitAccountsReady,
  editing,
  onSaved,
  onCancelEdit,
}: Props) {
  const { t, i18n } = useLingui();
  const amountRef = useRef<HTMLInputElement>(null);
  const [storedDraft, setDraft] = useState<Draft>(() =>
    emptyDraft(todayIso(), rememberedAccount()),
  );
  const [categoryChosen, setCategoryChosen] = useState(false);
  const [found, setFound] = useState<{ query: string; items: Suggestion[] }>({
    query: "",
    items: [],
  });
  const [applied, setApplied] = useState<Suggestion | null>(null);
  const [shownEditing, setShownEditing] = useState<Transaction | null>(null);
  const [invalid, setInvalid] = useState<Invalid>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const update = (changes: Partial<Draft>) =>
    setDraft((current) => ({ ...current, ...changes }));

  // A booking being edited keeps its account(s) as options even when archived, so opening it never
  // changes where it is booked; new entries only offer active accounts.
  const keptIds =
    editing === null
      ? []
      : [
          draftFrom(editing, i18n.locale).accountId,
          draftFrom(editing, i18n.locale).toAccountId,
        ];
  const options = accounts.concat(
    allAccounts.filter(
      (a) =>
        !accounts.some((active) => active.id === a.id) &&
        keptIds.includes(String(a.id)),
    ),
  );
  function accountLabel(a: Account): string {
    const { name } = a;
    return a.archived_at ? t`${name} (archived)` : name;
  }

  // The account is the stored one while it is offered, else the first active account. An edited
  // booking is never moved: its draft keeps the account it was loaded with.
  const accountKnown =
    editing !== null ||
    options.some((a) => String(a.id) === storedDraft.accountId);
  const draft: Draft = accountKnown
    ? storedDraft
    : { ...storedDraft, accountId: accounts[0] ? String(accounts[0].id) : "" };

  // `onSubmit` may await `waitAccountsReady()` mid-flight; by then this render's own `accounts`
  // (its closure variable) is stale, so `resolveAccountId()` re-reads the freshest value through a
  // ref kept in sync after every render (T-2026-CU-9130-043) instead of the async-invocation-time
  // closure. Synced from `useLayoutEffect`, not during render (refs are not render output) and not
  // a passive `useEffect`: layout effects flush synchronously, strictly before any component's
  // passive effects (including `EntryPage`'s `waitAccountsReady`-resolving one) — a passive effect
  // here would only be *usually* ordered before it by the child-before-parent convention, not
  // guaranteed.
  const accountsRef = useRef(accounts);
  useLayoutEffect(() => {
    accountsRef.current = accounts;
  });

  // Opening a booking from the list fills the form with it; when the edit ends from outside
  // (the booking was deleted), the form returns to a new entry. Adjusted while rendering.
  if (editing !== shownEditing) {
    setShownEditing(editing);
    if (editing !== null) {
      setDraft(draftFrom(editing, i18n.locale));
      setCategoryChosen(true);
      setApplied(null);
      setInvalid(null);
      setError(null);
      setNotice(null);
    } else if (shownEditing !== null) {
      setDraft(afterSave(draft));
      setCategoryChosen(false);
    }
  }

  useEffect(() => {
    if (editing !== null) amountRef.current?.focus();
  }, [editing]);

  // The shortcut focuses the amount when the screen is already open.
  useEffect(() => {
    const focus = () => amountRef.current?.focus();
    window.addEventListener(FOCUS_ENTRY_EVENT, focus);
    return () => window.removeEventListener(FOCUS_ENTRY_EVENT, focus);
  }, []);

  // Refocuses once a save finishes (success or failure) and the fields re-enable. Not called
  // directly from `onSubmit`'s `finally`: `setSaving(false)` there is only a state update request,
  // the field is still `disabled` in the DOM until this render commits, and a disabled field
  // cannot take focus (T-2026-CU-9130-043).
  useEffect(() => {
    if (!saving) amountRef.current?.focus();
  }, [saving]);

  // Suggestions for the typed text, debounced; an answer only counts for the query it belongs to.
  const query = draft.kind === "transfer" ? "" : draft.text.trim();
  const suggestions = query !== "" && found.query === query ? found.items : [];
  useEffect(() => {
    if (query === "") return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      suggest(query)
        .then((items) => !cancelled && setFound({ query, items }))
        .catch(() => !cancelled && setFound({ query, items: [] }));
    }, SUGGEST_DELAY_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  // A text used before fills category and counterparty once (UC-ZF-002 step 4, AK3), unless the
  // user picked a category. Adjusted while rendering.
  const match = categoryChosen
    ? undefined
    : suggestions.find((s) => sameText(s.text, draft.text));
  if (match && match !== applied) {
    setApplied(match);
    setDraft((current) => ({
      ...current,
      categoryId:
        match.category_id === null
          ? current.categoryId
          : String(match.category_id),
      counterparty: current.counterparty || (match.counterparty ?? ""),
    }));
  }
  const suggested =
    !categoryChosen &&
    applied !== null &&
    applied.category_id !== null &&
    sameText(applied.text, draft.text);

  const today = todayIso();
  const future = isFuture(draft.date, today);
  const kind = effectiveKind(draft);
  const category = categories.find((c) => String(c.id) === draft.categoryId);
  const mismatch = kindMismatch(draft, category);
  const account = options.find((a) => String(a.id) === draft.accountId);
  const balance = account
    ? formatCents(account.balance_cents, i18n.locale)
    : "";
  const categoryOptions =
    kind === "transfer"
      ? categories.filter((c) => c.kind === "transfer")
      : categories;

  function reset(next: Draft) {
    setDraft(next);
    setCategoryChosen(false);
    setApplied(null);
    setInvalid(null);
  }

  function cancelEdit() {
    reset(afterSave(draft));
    setError(null);
    setNotice(null);
    onCancelEdit();
    amountRef.current?.focus();
  }

  /** ``accountId`` is passed in (not read from ``draft``): after an await, ``draft`` may be stale;
   * see the ``draftRef``/``accountsRef`` comment above ``onSubmit``. */
  async function save(accountId: string): Promise<boolean> {
    const categoryId =
      draft.categoryId === "" ? null : Number(draft.categoryId);
    const memo = draft.memo.trim() || null;
    if (kind === "transfer") {
      const input = {
        date: draft.date,
        amount_cents: magnitudeCents(draft.amount) ?? 0,
        from_account_id: Number(accountId),
        to_account_id: Number(draft.toAccountId),
        category_id: categoryId,
        text: draft.text,
        memo,
      };
      const groupId = editing?.transfer_group_id;
      await (groupId ? updateTransfer(groupId, input) : createTransfer(input));
      return future;
    }
    const input = {
      date: draft.date,
      amount_cents: signedCents(draft) ?? 0,
      account_id: Number(accountId),
      category_id: categoryId,
      text: draft.text,
      counterparty: draft.counterparty.trim() || null,
      memo,
    };
    const saved = await (editing
      ? updateTransaction(editing.id, input)
      : createTransaction(input));
    return saved.status === "planned";
  }

  /** The account to save with: the chosen one, or — once accounts have loaded — the first active
   * one when none was chosen yet (mirrors the render-time default above). Reads the *fresh*
   * `accountsRef` (accounts may have loaded during `waitAccountsReady()`) against `draft.accountId`
   * — safe to read from the closure here because every field, `draft.accountId` included, is
   * frozen for the whole submit: the fieldset below is `disabled={saving}`, so nothing can change
   * it while this function runs (T-2026-CU-9130-043). */
  function resolveAccountId(): string {
    if (editing !== null && draft.accountId !== "") return draft.accountId;
    const freshAccounts = accountsRef.current;
    if (freshAccounts.some((a) => String(a.id) === draft.accountId)) {
      return draft.accountId;
    }
    return freshAccounts[0] ? String(freshAccounts[0].id) : "";
  }

  async function onSubmit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (saving) return;
    setNotice(null);
    if (magnitudeCents(draft.amount) === null) {
      setInvalid("amount");
      setError(t`Enter an amount other than 0, e.g. 12,50.`);
      amountRef.current?.focus();
      return;
    }
    // Freezes every field (the fieldset below turns `disabled`) for the rest of this function, so
    // a booking typed while this one is still awaiting the accounts fetch cannot be clobbered by
    // this call's own `reset()` once it resumes (T-2026-CU-9130-043) — `draft` below stays an
    // accurate snapshot of what the fields hold for as long as `saving` is true.
    setSaving(true);
    // A keyboard-only save can reach here before the accounts fetch the account default depends
    // on has resolved; wait for it (already resolved in the normal case, so this is a no-op)
    // before deciding whether an account is really missing.
    await waitAccountsReady();
    const accountId = resolveAccountId();
    if (accountId === "") {
      setInvalid("account");
      setError(t`Choose an account.`);
      setSaving(false);
      return;
    }
    if (
      kind === "transfer" &&
      (draft.toAccountId === "" || draft.toAccountId === accountId)
    ) {
      setInvalid("account");
      setError(t`Choose a different target account for the transfer.`);
      setSaving(false);
      return;
    }
    if (kind !== "transfer" && draft.categoryId === "") {
      setInvalid("category");
      setError(t`Choose a category.`);
      setSaving(false);
      return;
    }
    setInvalid(null);
    setError(null);
    try {
      const planned = await save(accountId);
      rememberAccount(accountId);
      const wasEditing = editing !== null;
      reset(afterSave({ ...draft, accountId }));
      if (wasEditing) onCancelEdit();
      setNotice(planned ? t`Saved as a planned item.` : t`Saved.`);
      await onSaved();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setSaving(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLFormElement>) {
    // Enter in a select would only close it; in the quick entry it saves like everywhere else.
    if (event.key === "Enter" && event.target instanceof HTMLSelectElement) {
      event.preventDefault();
      void onSubmit();
    }
    if (event.key === "Escape" && editing) {
      event.preventDefault();
      cancelEdit();
    }
  }

  const kinds: { value: Kind; label: string }[] = [
    { value: "expense", label: t`Expense` },
    { value: "income", label: t`Income` },
    { value: "transfer", label: t`Transfer` },
  ];

  return (
    <form
      className="entry-form"
      onSubmit={(event) => void onSubmit(event)}
      onKeyDown={onKeyDown}
      noValidate
      aria-label={t`Quick entry`}
    >
      {/* Disabled while a save is in flight, so a booking typed for the next entry cannot land in
          the same fields this submit's own reset() is about to overwrite once it resumes
          (T-2026-CU-9130-043) — not while merely loading (see "Fix" in the SPEC: that variant was
          tried and discarded, keystrokes sent to a disabled field are lost, not queued). Brief in
          the common case (`saving` only spans network round-trips), so this trades no responsiveness
          for AK1's timed loop, which already waits for the previous booking's notice first. */}
      <fieldset disabled={saving} className="entry-fieldset">
        <div className="entry-head">
          <fieldset className="ui-segmented">
            <legend className="sr-only">
              <Trans>Kind of booking</Trans>
            </legend>
            {kinds.map((option) => (
              <label key={option.value}>
                <input
                  type="radio"
                  name="entry-kind"
                  value={option.value}
                  checked={kind === option.value}
                  onChange={() =>
                    update({
                      kind: option.value,
                      amount: draft.amount.replace(/^\s*-/, ""),
                      ...(option.value === "transfer" ||
                      draft.kind === "transfer"
                        ? { categoryId: "" }
                        : {}),
                    })
                  }
                />
                {option.label}
              </label>
            ))}
          </fieldset>
          {editing && (
            <p className="entry-editing">
              <Trans>Editing a booking — Esc cancels.</Trans>
            </p>
          )}
        </div>

        <div className="ui-form-grid">
          <Field htmlFor="entry-date" label={<Trans>Date</Trans>}>
            <input
              id="entry-date"
              type="date"
              required
              value={draft.date}
              onChange={(event) => update({ date: event.target.value })}
            />
          </Field>
          <div className="ui-field">
            <label htmlFor="entry-account">
              {kind === "transfer" ? (
                <Trans>From account</Trans>
              ) : (
                <Trans>Account</Trans>
              )}
            </label>
            <select
              id="entry-account"
              value={draft.accountId}
              aria-invalid={invalid === "account"}
              onChange={(event) => update({ accountId: event.target.value })}
            >
              {accounts.length === 0 && (
                <option value="">{t`No account`}</option>
              )}
              {options.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a)}
                </option>
              ))}
            </select>
            {account && (
              <span className="ui-hint">
                <Trans>Balance today: {balance}</Trans>
              </span>
            )}
          </div>
          {kind === "transfer" && (
            <div className="ui-field">
              <label htmlFor="entry-to-account">
                <Trans>To account</Trans>
              </label>
              <select
                id="entry-to-account"
                value={draft.toAccountId}
                aria-invalid={invalid === "account"}
                onChange={(event) =>
                  update({ toAccountId: event.target.value })
                }
              >
                <option value="">{t`Choose…`}</option>
                {options
                  .filter((a) => String(a.id) !== draft.accountId)
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {accountLabel(a)}
                    </option>
                  ))}
              </select>
            </div>
          )}
        </div>

        <div className="ui-form-grid entry-grid-main">
          <div className="ui-field">
            <label htmlFor="entry-amount">
              <Trans>Amount (€)</Trans>
            </label>
            <div className={`entry-amount entry-amount-${kind}`}>
              <span aria-hidden="true">
                {kind === "expense" ? "−" : kind === "income" ? "+" : "⇄"}
              </span>
              <input
                id="entry-amount"
                ref={amountRef}
                autoFocus
                inputMode="decimal"
                autoComplete="off"
                placeholder="0,00"
                value={draft.amount}
                aria-invalid={invalid === "amount"}
                onChange={(event) => update({ amount: event.target.value })}
              />
            </div>
          </div>
          <div className="ui-field">
            <label htmlFor="entry-text">
              <Trans>Text</Trans>
            </label>
            <input
              id="entry-text"
              list="entry-text-suggestions"
              autoComplete="off"
              maxLength={200}
              value={draft.text}
              onChange={(event) => update({ text: event.target.value })}
            />
            <datalist id="entry-text-suggestions">
              {suggestions.map((s) => (
                <option key={s.text} value={s.text} />
              ))}
            </datalist>
          </div>
          <div className="ui-field">
            <label htmlFor="entry-category">
              <Trans>Category</Trans>
            </label>
            <select
              id="entry-category"
              value={draft.categoryId}
              aria-invalid={invalid === "category"}
              onChange={(event) => {
                update({ categoryId: event.target.value });
                setCategoryChosen(true);
              }}
            >
              <option value="">
                {kind === "transfer" ? t`No category` : t`Choose…`}
              </option>
              {categoryOptions.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({i18n._(CATEGORY_KIND_LABELS[c.kind])})
                </option>
              ))}
            </select>
            {suggested && (
              <span className="ui-hint">
                <Trans>Suggested from earlier bookings.</Trans>
              </span>
            )}
            {mismatch && (
              <span className="ui-hint entry-warning" role="status">
                <Trans>
                  The category kind does not match the amount. Saving is still
                  possible (refund, reversal).
                </Trans>
              </span>
            )}
          </div>
        </div>

        <div className="ui-form-grid">
          {kind !== "transfer" && (
            <Field
              htmlFor="entry-counterparty"
              label={<Trans>Counterparty (optional)</Trans>}
            >
              <input
                id="entry-counterparty"
                autoComplete="off"
                maxLength={100}
                value={draft.counterparty}
                onChange={(event) =>
                  update({ counterparty: event.target.value })
                }
              />
            </Field>
          )}
          <Field
            htmlFor="entry-memo"
            label={<Trans>Note (optional)</Trans>}
            wide
          >
            <input
              id="entry-memo"
              autoComplete="off"
              maxLength={1000}
              value={draft.memo}
              onChange={(event) => update({ memo: event.target.value })}
            />
          </Field>
        </div>

        {future && (
          <p className="ui-hint entry-planned" role="status">
            <Trans>
              The date is in the future: this will be saved as a planned item
              and does not change the balance yet.
            </Trans>
          </p>
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}

        <div className="ui-actions">
          <Button variant="primary" type="submit" disabled={saving}>
            {editing ? <Trans>Save changes</Trans> : <Trans>Save</Trans>}
          </Button>
          {editing && (
            <button type="button" onClick={cancelEdit}>
              <Trans>Cancel</Trans>
            </button>
          )}
          <span className="ui-hint">
            <Trans>Enter saves · Alt+N opens this form from any page</Trans>
          </span>
          {notice && (
            <span className="ui-notice" role="status">
              {notice}
            </span>
          )}
        </div>
      </fieldset>
    </form>
  );
}
