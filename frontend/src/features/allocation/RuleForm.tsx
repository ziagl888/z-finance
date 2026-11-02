import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type FormEvent } from "react";

import { centsToInput, parseEuroInput } from "../accounts/accountsApi";
import { Button, Field } from "../../ui";
import {
  FULL_BP,
  formatBp,
  percentSum,
  type FallbackKind,
  type PotOption,
  type RuleInput,
  type RuleTarget,
  type TargetKind,
} from "./allocationApi";

export interface RuleFormProps {
  /** The rule being edited, or a fresh one from `emptyRule()`. */
  value: RuleInput;
  pots: PotOption[];
  /** The levy rate in basis points: the levy line is added on top of these shares. */
  levyRateBp: number;
  editing: boolean;
  onSave: (input: RuleInput) => Promise<void>;
  onCancel: () => void;
}

/**
 * Rule editor (UC-ZF-014 step 1): trigger (area, minimum amount), the shares in payment order and
 * the fallback for the debt share. The levy share is not entered here — it comes from the levy rate
 * and is already reserved (the core rule); the form only shows what it will add.
 */
export function RuleForm({
  value,
  pots,
  levyRateBp,
  editing,
  onSave,
  onCancel,
}: RuleFormProps) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const [name, setName] = useState(value.name);
  const [area, setArea] = useState<"private" | "business" | "any">(
    value.area ?? "any",
  );
  const [minimum, setMinimum] = useState(
    value.min_amount_cents === 0
      ? ""
      : centsToInput(value.min_amount_cents, locale),
  );
  const [targets, setTargets] = useState<RuleTarget[]>(value.targets);
  const [fallbackKind, setFallbackKind] = useState<FallbackKind>(
    value.fallback_kind,
  );
  const [fallbackPot, setFallbackPot] = useState<number | null>(
    value.fallback_pot_id,
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const sum = percentSum(targets);
  const share = formatBp(sum, locale);
  const levyShare = formatBp(levyRateBp, locale);

  function change(index: number, patch: Partial<RuleTarget>) {
    setTargets((current) =>
      current.map((target, at) =>
        at === index ? { ...target, ...patch } : target,
      ),
    );
  }

  function addTarget() {
    setTargets((current) => [
      ...current,
      {
        kind: "pot",
        pot_id: pots[0]?.id ?? null,
        percent_bp: 500,
        fixed_cents: null,
      },
    ]);
  }

  function removeTarget(index: number) {
    setTargets((current) => current.filter((_, at) => at !== index));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const minimumCents = minimum.trim() === "" ? 0 : parseEuroInput(minimum);
    if (name.trim() === "") {
      setError(t`Enter a name for the rule.`);
      return;
    }
    if (minimumCents === null || minimumCents < 0) {
      setError(t`Enter the minimum amount as 0 or more, e.g. 500.`);
      return;
    }
    if (targets.length === 0) {
      setError(t`Add at least one target the income should go to.`);
      return;
    }
    if (sum > FULL_BP) {
      setError(t`The shares add up to more than 100 % of the income.`);
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await onSave({
        name: name.trim(),
        area: area === "any" ? null : area,
        category_id: value.category_id,
        min_amount_cents: minimumCents,
        targets,
        fallback_kind: fallbackKind,
        fallback_pot_id: fallbackKind === "pot" ? fallbackPot : null,
        active: value.active,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  const kinds: { value: TargetKind; label: string }[] = [
    { value: "debt_plan", label: t`Debt payoff` },
    { value: "pot", label: t`Pot` },
    { value: "reserve", label: t`Total reserve` },
  ];

  return (
    <form
      className="allocation-form"
      onSubmit={(event) => void onSubmit(event)}
      noValidate
      aria-label={editing ? t`Edit rule` : t`New rule`}
    >
      <h2>{editing ? <Trans>Edit rule</Trans> : <Trans>New rule</Trans>}</h2>
      <div className="ui-form-grid">
        <Field htmlFor="allocation-name" label={<Trans>Name</Trans>}>
          <input
            id="allocation-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field
          htmlFor="allocation-minimum"
          label={<Trans>From this amount</Trans>}
          hint={<Trans>Leave empty to use every income.</Trans>}
        >
          <input
            id="allocation-minimum"
            className="num"
            inputMode="decimal"
            value={minimum}
            onChange={(e) => setMinimum(e.target.value)}
          />
        </Field>
        <Field htmlFor="allocation-area" label={<Trans>Income of</Trans>}>
          <select
            id="allocation-area"
            value={area}
            onChange={(e) =>
              setArea(e.target.value as "private" | "business" | "any")
            }
          >
            <option value="business">{t`Business accounts`}</option>
            <option value="private">{t`Private accounts`}</option>
            <option value="any">{t`All accounts`}</option>
          </select>
        </Field>
      </div>

      <h3>
        <Trans>Shares</Trans>
      </h3>
      <p className="ui-hint">
        {levyRateBp > 0 ? (
          <Trans>
            The levy share of {levyShare} % is added on top and goes into your
            levy pot. It is already set aside from the moment the income is
            booked, so you cannot lower it.
          </Trans>
        ) : (
          <Trans>
            No levy rate is set, so no levy share is proposed. You can set it
            under Taxes &amp; levies.
          </Trans>
        )}
      </p>
      <ul className="allocation-targets">
        {targets.map((target, index) => (
          <li key={index}>
            <select
              aria-label={t`Target ${index + 1}`}
              value={target.kind}
              onChange={(e) =>
                change(index, {
                  kind: e.target.value as TargetKind,
                  pot_id:
                    e.target.value === "pot" ? (pots[0]?.id ?? null) : null,
                })
              }
            >
              {kinds.map((kind) => (
                <option key={kind.value} value={kind.value}>
                  {kind.label}
                </option>
              ))}
            </select>
            {target.kind === "pot" && (
              <select
                aria-label={t`Pot of target ${index + 1}`}
                value={target.pot_id ?? ""}
                onChange={(e) =>
                  change(index, { pot_id: Number(e.target.value) })
                }
              >
                {pots.map((pot) => (
                  <option key={pot.id} value={pot.id}>
                    {pot.name}
                  </option>
                ))}
              </select>
            )}
            <input
              className="num"
              inputMode="decimal"
              aria-label={t`Percentage of target ${index + 1}`}
              value={
                target.percent_bp === null
                  ? ""
                  : formatBp(target.percent_bp, locale)
              }
              onChange={(e) =>
                change(index, {
                  percent_bp: percentToBp(e.target.value),
                  fixed_cents: null,
                })
              }
            />
            <span aria-hidden="true">%</span>
            <Button
              variant="danger"
              aria-label={t`Remove target ${index + 1}`}
              onClick={() => removeTarget(index)}
            >
              ✕
            </Button>
          </li>
        ))}
      </ul>
      <p className="ui-hint">
        <Trans>The shares add up to {share} % of the income.</Trans>
      </p>
      <div className="ui-actions">
        <Button onClick={addTarget}>
          <Trans>Add share</Trans>
        </Button>
      </div>

      <Field
        htmlFor="allocation-fallback"
        label={<Trans>Without an open debt, the debt share goes to</Trans>}
      >
        <select
          id="allocation-fallback"
          value={fallbackKind}
          onChange={(e) => setFallbackKind(e.target.value as FallbackKind)}
        >
          <option value="reserve">{t`The total reserve`}</option>
          <option value="pot">{t`A pot`}</option>
        </select>
      </Field>
      {fallbackKind === "pot" && (
        <Field htmlFor="allocation-fallback-pot" label={<Trans>Pot</Trans>}>
          <select
            id="allocation-fallback-pot"
            value={fallbackPot ?? ""}
            onChange={(e) =>
              setFallbackPot(
                e.target.value === "" ? null : Number(e.target.value),
              )
            }
          >
            <option value="">{t`The highest-ranked pot of the account`}</option>
            {pots.map((pot) => (
              <option key={pot.id} value={pot.id}>
                {pot.name}
              </option>
            ))}
          </select>
        </Field>
      )}

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="ui-actions">
        <Button variant="primary" type="submit" disabled={busy}>
          <Trans>Save rule</Trans>
        </Button>
        <Button onClick={onCancel}>
          <Trans>Cancel</Trans>
        </Button>
      </div>
    </form>
  );
}

/** A typed percentage ("20", "12,5") as basis points; an unreadable entry keeps 0.
 *
 * A share above 100 % is kept as typed so the form can say so (see `onSubmit`); silently clamping
 * it would change the user's rule behind their back.
 */
function percentToBp(text: string): number {
  const cents = parseEuroInput(text);
  return cents === null ? 0 : Math.max(0, cents);
}
