import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState } from "react";

import { formatCents } from "../../api/client";
import {
  formatMonth,
  getWhatIf,
  type WhatIf as WhatIfResult,
} from "./payoffApi";

/** Steps of the slider: 10 € up to ±1,000 € a month. */
const STEP_CENTS = 1_000;
const MAX_CENTS = 100_000;

/**
 * "What if": a slider for more or less money a month and the debt-free month it gives (UC step 7,
 * AK5). It only asks the server; nothing is saved.
 */
export function WhatIf({
  budgetCents,
  debtFreeMonth,
}: {
  budgetCents: number;
  debtFreeMonth: string | null;
}) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const [delta, setDelta] = useState(0);
  // The answer remembers its delta, so a stale one is never shown for another position.
  const [answer, setAnswer] = useState<{
    delta: number;
    result: WhatIfResult;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (delta === 0) return;
    let cancelled = false;
    // Wait until the slider rests before asking the server.
    const timer = window.setTimeout(() => {
      getWhatIf(delta)
        .then((loaded) => {
          if (cancelled) return;
          setAnswer({ delta, result: loaded });
          setError(null);
        })
        .catch(
          (reason: unknown) =>
            !cancelled &&
            setError(reason instanceof Error ? reason.message : String(reason)),
        );
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [delta]);

  const result = answer?.delta === delta ? answer.result : null;
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "±";
  const change = `${sign}${formatCents(Math.abs(delta), locale)}`;
  const now = debtFreeMonth ? formatMonth(debtFreeMonth, locale) : "—";
  const then =
    result?.debt_free_month != null
      ? formatMonth(result.debt_free_month, locale)
      : "—";

  return (
    <div className="payoff-whatif">
      <h2>
        <Trans>What if?</Trans>
      </h2>
      <p className="ui-hint">
        <Trans>
          Try more or less money a month and see when you would be debt-free.
          This does not change your plan.
        </Trans>
      </p>
      <label htmlFor="payoff-whatif">
        <Trans>Change per month: {change}</Trans>
      </label>
      <input
        id="payoff-whatif"
        type="range"
        min={-Math.min(budgetCents, MAX_CENTS)}
        max={MAX_CENTS}
        step={STEP_CENTS}
        value={delta}
        aria-valuetext={t`${change} per month`}
        onChange={(e) => setDelta(Number(e.target.value))}
      />
      <p role="status" className="payoff-whatif-result">
        {delta === 0 || result === null ? (
          <Trans>Debt-free: {now}</Trans>
        ) : result.has_plan && then !== now ? (
          <Trans>
            Debt-free: {then} instead of {now}
          </Trans>
        ) : (
          <Trans>No plan possible with this amount.</Trans>
        )}
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </div>
  );
}
