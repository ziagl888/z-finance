import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type DragEvent, type FormEvent } from "react";

import { formatCents } from "../../api/client";
import { centsToInput, parseEuroInput } from "../accounts/accountsApi";
import { Button, Field } from "../../ui";
import {
  moveItem,
  type DebtLine,
  type PayoffStatus,
  type PlanInput,
  type Strategy,
} from "./payoffApi";

export interface PlanFormProps {
  status: PayoffStatus;
  onSave: (input: PlanInput) => Promise<void>;
}

/**
 * Budget, strategy, the own order of the debts (drag and drop, or the up/down buttons) and the
 * income override (UC-ZF-012 steps 3-4, 4a). Nothing is stored until "Save plan".
 */
export function PlanForm({ status, onSave }: PlanFormProps) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const [strategy, setStrategy] = useState<Strategy>(status.strategy);
  const [budget, setBudget] = useState(
    centsToInput(status.budget_cents, locale),
  );
  const [income, setIncome] = useState(
    status.income_override_cents === null
      ? ""
      : centsToInput(status.income_override_cents, locale),
  );
  const [order, setOrder] = useState<DebtLine[]>(() => ownOrder(status));
  const [dragged, setDragged] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const strategies: { value: Strategy; label: string }[] = [
    { value: "snowball", label: t`Smallest first` },
    { value: "avalanche", label: t`Most expensive first` },
    { value: "manual", label: t`My own order` },
  ];
  const budgetCents = parseEuroInput(budget);
  const installments = formatCents(
    status.availability.installments_cents,
    locale,
  );
  const total =
    budgetCents === null
      ? null
      : formatCents(
          status.availability.installments_cents + budgetCents,
          locale,
        );
  const suggestedCents = status.suggested_budget_cents;
  const suggested =
    suggestedCents === null ? null : formatCents(suggestedCents, locale);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const incomeCents = income.trim() === "" ? null : parseEuroInput(income);
    if (budgetCents === null || budgetCents < 0) {
      setError(t`Enter a budget of 0 or more, e.g. 300.`);
      return;
    }
    if (incomeCents !== null && incomeCents < 0) {
      setError(
        t`Enter the income as an amount of 0 or more, or leave it empty.`,
      );
      return;
    }
    if (income.trim() !== "" && incomeCents === null) {
      setError(t`Enter the income as an amount, e.g. 3000, or leave it empty.`);
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await onSave({
        strategy,
        budget_cents: budgetCents,
        manual_order: strategy === "manual" ? order.map((d) => d.id) : [],
        income_override_cents: incomeCents,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  function move(from: number, to: number) {
    setOrder((current) => moveItem(current, from, to));
  }

  function onDrop(event: DragEvent<HTMLLIElement>, to: number) {
    event.preventDefault();
    if (dragged !== null) move(dragged, to);
    setDragged(null);
  }

  return (
    <form
      className="payoff-form"
      onSubmit={(event) => void onSubmit(event)}
      noValidate
      aria-label={t`Your plan`}
    >
      <h2>
        <Trans>Your plan</Trans>
      </h2>
      <fieldset className="ui-segmented">
        <legend className="sr-only">
          <Trans>Order of the debts</Trans>
        </legend>
        {strategies.map((option) => (
          <label key={option.value}>
            <input
              type="radio"
              name="payoff-strategy"
              value={option.value}
              checked={strategy === option.value}
              onChange={() => setStrategy(option.value)}
            />
            {option.label}
          </label>
        ))}
      </fieldset>
      <p className="ui-hint">
        {strategy === "snowball" && (
          <Trans>
            The smallest debt is paid off first: you see progress soonest.
          </Trans>
        )}
        {strategy === "avalanche" && (
          <Trans>
            The debt with the highest interest rate is paid off first: it costs
            the least interest.
          </Trans>
        )}
        {strategy === "manual" && (
          <Trans>
            Put the most pressing debt (a reminder, the tax office) at the top:
            drag it, or use the arrow buttons.
          </Trans>
        )}
      </p>
      {strategy === "manual" && (
        <ol className="payoff-order" aria-label={t`Your order`}>
          {order.map((debt, index) => {
            const name = debt.creditor;
            return (
              <li
                key={debt.id}
                draggable
                className={dragged === index ? "payoff-dragging" : undefined}
                onDragStart={() => setDragged(index)}
                onDragEnd={() => setDragged(null)}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => onDrop(event, index)}
              >
                <span className="payoff-grip" aria-hidden="true">
                  ⋮⋮
                </span>
                <span className="payoff-order-name">{debt.creditor}</span>
                <span className="num muted">
                  {formatCents(debt.remaining_cents, locale)}
                </span>
                <Button
                  aria-label={t`Move ${name} up`}
                  disabled={index === 0}
                  onClick={() => move(index, index - 1)}
                >
                  ↑
                </Button>
                <Button
                  aria-label={t`Move ${name} down`}
                  disabled={index === order.length - 1}
                  onClick={() => move(index, index + 1)}
                >
                  ↓
                </Button>
              </li>
            );
          })}
        </ol>
      )}
      <div className="ui-form-grid">
        <Field
          htmlFor="payoff-budget"
          label={<Trans>Extra per month</Trans>}
          hint={
            total === null ? (
              <Trans>On top of the instalments ({installments}).</Trans>
            ) : (
              <Trans>
                On top of the instalments ({installments}): {total} a month in
                total.
              </Trans>
            )
          }
        >
          <input
            id="payoff-budget"
            className="num"
            inputMode="decimal"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
        </Field>
        <Field
          htmlFor="payoff-income"
          label={<Trans>Monthly income to plan with</Trans>}
          hint={
            <Trans>
              Leave empty to use the average of the last three months.
            </Trans>
          }
        >
          <input
            id="payoff-income"
            className="num"
            inputMode="decimal"
            value={income}
            placeholder={t`Average`}
            onChange={(e) => setIncome(e.target.value)}
          />
        </Field>
      </div>
      {suggestedCents !== null && suggested !== null && (
        <p className="ui-hint" role="status">
          <Trans>
            Your income has risen. You could put {suggested} a month on top of
            the instalments.
          </Trans>{" "}
          <Button
            onClick={() => setBudget(centsToInput(suggestedCents, locale))}
          >
            <Trans>Use this amount</Trans>
          </Button>
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="ui-actions">
        <Button variant="primary" type="submit" disabled={busy}>
          <Trans>Save plan</Trans>
        </Button>
      </div>
    </form>
  );
}

/** The debts in the saved own order; debts not in it follow in plan order. */
function ownOrder(status: PayoffStatus): DebtLine[] {
  const byId = new Map(status.debts.map((d) => [d.id, d]));
  const first = status.manual_order
    .map((id) => byId.get(id))
    .filter((d): d is DebtLine => d !== undefined);
  return [...first, ...status.debts.filter((d) => !first.includes(d))];
}
