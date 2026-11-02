import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";

import { isLocale, LOCALES, useLocale } from "../../i18n";
import { Button, Card, Field, Table } from "../../ui";
import {
  ACCOUNT_TYPES,
  AREAS,
  completeSetup,
  createAccount,
  getSetup,
  listAccounts,
  parseEuroInput,
  type Area,
  type AccountType,
  type CategoryDraft,
  type CategoryKind,
  CATEGORY_KINDS,
} from "../accounts/accountsApi";
import {
  ACCOUNT_TYPE_LABELS,
  AREA_LABELS,
  CATEGORY_KIND_LABELS,
} from "../accounts/labels";
import {
  getLevySettings,
  parseRateInput,
  putLevySettings,
  todayIso,
} from "./firstRunApi";
import "./first-run.css";

/** The four things z-finance needs before it is useful (UC NF10). */
const STEPS = ["language", "account", "categories", "levy"] as const;
type Step = (typeof STEPS)[number];

interface AccountForm {
  name: string;
  area: Area;
  type: AccountType;
  balance: string;
  date: string;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * First-run wizard (UC NF10, SPEC AK20): language, first account, default categories and — if the
 * user wants — the levy rate, in four steps with a back button.
 *
 * Nothing is written before **Finish**: the account is created, an entered levy rate is stored and
 * the category set is adopted, in that order, so the run that ends the first run is the last one.
 * The shell sends a first-time user here (`redirect.ts`); the screen is also reachable by its URL,
 * and says so when there is nothing left to set up.
 */
export function FirstRunWizard() {
  const { t, i18n } = useLingui();
  const { locale, setLocale } = useLocale();
  const navigate = useNavigate();

  const [step, setStep] = useState<Step>("language");
  const [offered, setOffered] = useState<boolean | null>(null);
  const [drafts, setDrafts] = useState<CategoryDraft[]>([]);
  const [account, setAccount] = useState<AccountForm>({
    name: "",
    area: "private",
    type: "giro",
    balance: "0",
    date: todayIso(),
  });
  const [rate, setRate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const createdAccountId = useRef<number | null>(null);

  // The set is offered in the UI language, so switching the language in step 1 reloads it.
  useEffect(() => {
    let cancelled = false;
    getSetup(locale)
      .then((state) => {
        if (cancelled) return;
        setOffered(state.offer_default_set);
        if (state.offer_default_set) setDrafts(state.default_categories);
      })
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, [locale]);

  if (offered === false) {
    return (
      <section className="first-run">
        <Card>
          <h1>
            <Trans>Everything is set up</Trans>
          </h1>
          <p>
            <Trans>
              z-finance is ready. Open the start page to see your figures.
            </Trans>
          </p>
          <Link to="/">
            <Trans>To the start page</Trans>
          </Link>
        </Card>
      </section>
    );
  }

  const index = STEPS.indexOf(step);
  const balanceCents = parseEuroInput(account.balance);
  const rateBasisPoints = rate.trim() === "" ? null : parseRateInput(rate);
  const accountReady = account.name.trim() !== "" && balanceCents !== null;
  const rateReady = rate.trim() === "" || rateBasisPoints !== null;
  const canContinue =
    (step !== "account" || accountReady) && (step !== "levy" || rateReady);

  const changeDraft = (position: number, changes: Partial<CategoryDraft>) =>
    setDrafts(
      drafts.map((draft, other) =>
        other === position ? { ...draft, ...changes } : draft,
      ),
    );

  /**
   * Create the first account once. A failed Finish can be repeated: the id of the created account is
   * remembered, and without one an account of the same name that already exists is taken over.
   */
  async function ensureAccount(openingBalanceCents: number) {
    if (createdAccountId.current !== null) return;
    const name = account.name.trim();
    // The backend keeps account names unique without regard to case and across archived accounts,
    // so the match has to look at all accounts the same way.
    const wanted = name.toLowerCase();
    const existing = (await listAccounts(true)).find(
      (candidate) => candidate.name.toLowerCase() === wanted,
    );
    if (existing) {
      createdAccountId.current = existing.id;
      return;
    }
    const created = await createAccount({
      name,
      area: account.area,
      type: account.type,
      opening_balance_cents: openingBalanceCents,
      opening_date: account.date,
      reserve_floor_cents: 0,
    });
    createdAccountId.current = created.id;
  }

  async function finish() {
    if (balanceCents === null) return;
    setBusy(true);
    setError(null);
    try {
      await ensureAccount(balanceCents);
      if (rateBasisPoints !== null) {
        const current = await getLevySettings();
        await putLevySettings({
          levy_rate_bp: rateBasisPoints,
          levy_base: current.levy_base,
          due_month_day: current.due_month_day,
          levy_pot_id: current.levy_pot_id,
          account_id: current.account_id,
        });
      }
      await completeSetup(drafts.filter((draft) => draft.name.trim() !== ""));
      await navigate("/", { replace: true });
    } catch (reason) {
      setError(message(reason));
      setBusy(false);
    }
  }

  return (
    <section className="first-run">
      <h1>
        <Trans>Welcome to z-finance</Trans>
      </h1>
      <p className="first-run-lead">
        <Trans>
          Four short steps and you can start. You can change everything later.
        </Trans>
      </p>

      <ol className="first-run-steps">
        {STEPS.map((name, position) => (
          <li
            key={name}
            aria-current={name === step ? "step" : undefined}
            className={position <= index ? "is-reached" : undefined}
          >
            {i18n._(STEP_TITLES[name])}
          </li>
        ))}
      </ol>

      <Card as="section" aria-labelledby="first-run-step-title">
        {step === "language" && (
          <>
            <h2 id="first-run-step-title">
              <Trans>Which language?</Trans>
            </h2>
            <Field
              htmlFor="first-run-language"
              label={<Trans>Language</Trans>}
              hint={
                <Trans>
                  The interface, the help and the manual follow this choice.
                </Trans>
              }
            >
              <select
                id="first-run-language"
                value={locale}
                onChange={(event) => {
                  const value = event.target.value;
                  if (isLocale(value)) void setLocale(value);
                }}
              >
                {Object.entries(LOCALES).map(([code, name]) => (
                  <option key={code} value={code} lang={code}>
                    {name}
                  </option>
                ))}
              </select>
            </Field>
          </>
        )}

        {step === "account" && (
          <>
            <h2 id="first-run-step-title">
              <Trans>Your first account</Trans>
            </h2>
            <p>
              <Trans>
                The account you use most — your current account, for example.
                Enter the balance it has today; every booking you record later
                builds on it.
              </Trans>
            </p>
            <div className="ui-form-grid">
              <Field htmlFor="first-run-name" label={<Trans>Name</Trans>}>
                <input
                  id="first-run-name"
                  required
                  maxLength={100}
                  value={account.name}
                  onChange={(event) =>
                    setAccount({ ...account, name: event.target.value })
                  }
                />
              </Field>
              <Field htmlFor="first-run-area" label={<Trans>Area</Trans>}>
                <select
                  id="first-run-area"
                  value={account.area}
                  onChange={(event) =>
                    setAccount({
                      ...account,
                      area: event.target.value as Area,
                    })
                  }
                >
                  {AREAS.map((area) => (
                    <option key={area} value={area}>
                      {i18n._(AREA_LABELS[area])}
                    </option>
                  ))}
                </select>
              </Field>
              <Field htmlFor="first-run-type" label={<Trans>Type</Trans>}>
                <select
                  id="first-run-type"
                  value={account.type}
                  onChange={(event) =>
                    setAccount({
                      ...account,
                      type: event.target.value as AccountType,
                    })
                  }
                >
                  {ACCOUNT_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {i18n._(ACCOUNT_TYPE_LABELS[type])}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                htmlFor="first-run-balance"
                label={<Trans>Balance today</Trans>}
                error={
                  balanceCents === null ? (
                    <Trans>Enter an amount such as 1234,56.</Trans>
                  ) : undefined
                }
              >
                <input
                  id="first-run-balance"
                  inputMode="decimal"
                  value={account.balance}
                  onChange={(event) =>
                    setAccount({ ...account, balance: event.target.value })
                  }
                />
              </Field>
              <Field htmlFor="first-run-date" label={<Trans>As of</Trans>}>
                <input
                  id="first-run-date"
                  type="date"
                  value={account.date}
                  onChange={(event) =>
                    setAccount({ ...account, date: event.target.value })
                  }
                />
              </Field>
            </div>
          </>
        )}

        {step === "categories" && (
          <>
            <h2 id="first-run-step-title">
              <Trans>Categories to start with</Trans>
            </h2>
            <p>
              <Trans>
                z-finance suggests a set for private and business use. Rename
                what you call differently, remove what you do not need — nothing
                is saved before the last step.
              </Trans>
            </p>
            <Table>
              <thead>
                <tr>
                  <th>
                    <Trans>Name</Trans>
                  </th>
                  <th>
                    <Trans>Kind</Trans>
                  </th>
                  <th>
                    <Trans>Area</Trans>
                  </th>
                  <th>
                    <span className="sr-only">
                      <Trans>Actions</Trans>
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {drafts.map((draft, position) => (
                  <tr key={position}>
                    <td>
                      <input
                        aria-label={t`Name`}
                        maxLength={100}
                        value={draft.name}
                        onChange={(event) =>
                          changeDraft(position, { name: event.target.value })
                        }
                      />
                    </td>
                    <td>
                      <select
                        aria-label={t`Kind`}
                        value={draft.kind}
                        onChange={(event) =>
                          changeDraft(position, {
                            kind: event.target.value as CategoryKind,
                          })
                        }
                      >
                        {CATEGORY_KINDS.map((kind) => (
                          <option key={kind} value={kind}>
                            {i18n._(CATEGORY_KIND_LABELS[kind])}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        aria-label={t`Area`}
                        value={draft.area ?? ""}
                        onChange={(event) =>
                          changeDraft(position, {
                            area: (event.target.value || null) as Area | null,
                          })
                        }
                      >
                        <option value="">{t`Both areas`}</option>
                        {AREAS.map((area) => (
                          <option key={area} value={area}>
                            {i18n._(AREA_LABELS[area])}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <button
                        type="button"
                        onClick={() =>
                          setDrafts(
                            drafts.filter((_, other) => other !== position),
                          )
                        }
                      >
                        <Trans>Remove</Trans>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </>
        )}

        {step === "levy" && (
          <>
            <h2 id="first-run-step-title">
              <Trans>Taxes and contributions</Trans>
            </h2>
            <p>
              <Trans>
                If you run a business, z-finance can set part of every payment
                you receive aside for income tax and social insurance. Leave the
                field empty if that does not apply to you — you can set it later
                under Levy &amp; revenue.
              </Trans>
            </p>
            <Field
              htmlFor="first-run-rate"
              label={<Trans>Set aside (%)</Trans>}
              hint={<Trans>For example 35 — leave empty to skip.</Trans>}
              error={
                rateReady ? undefined : (
                  <Trans>Enter a rate between 0 and 90, such as 35.</Trans>
                )
              }
            >
              <input
                id="first-run-rate"
                inputMode="decimal"
                value={rate}
                onChange={(event) => setRate(event.target.value)}
              />
            </Field>
          </>
        )}

        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}

        <div className="ui-actions">
          <Button
            type="button"
            disabled={index === 0 || busy}
            onClick={() => setStep(STEPS[index - 1] ?? "language")}
          >
            <Trans>Back</Trans>
          </Button>
          {step === "levy" ? (
            <Button
              variant="primary"
              type="button"
              disabled={busy || !rateReady}
              onClick={() => void finish()}
            >
              <Trans>Finish</Trans>
            </Button>
          ) : (
            <Button
              variant="primary"
              type="button"
              disabled={!canContinue || offered === null}
              onClick={() => setStep(STEPS[index + 1] ?? "levy")}
            >
              <Trans>Continue</Trans>
            </Button>
          )}
        </div>
      </Card>
    </section>
  );
}

/** Step titles for the progress list; module-level constants, so `msg` and `i18n._()`. */
const STEP_TITLES: Record<Step, MessageDescriptor> = {
  language: msg`Language`,
  account: msg`First account`,
  categories: msg`Categories`,
  levy: msg`Taxes`,
};
