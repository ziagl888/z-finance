import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState, type FormEvent } from "react";

import { formatCents } from "../../api/client";
import { Button, Field } from "../../ui";
import {
  centsToInput,
  getAccountSettings,
  parseEuroInput,
  putAccountSettings,
} from "./accountsApi";
import "./accounts.css";

type Status =
  | { kind: "idle" }
  | { kind: "saved"; cents: number }
  | { kind: "error"; text: string };

/**
 * The total reserve floor over all accounts (UC-ZF-001 TBD-5, SPEC AK12), shown as a section of
 * the settings page. Only stored here; the liquidity forecast (UC-ZF-006) evaluates it.
 */
export function TotalReserveSetting() {
  const { t, i18n } = useLingui();
  const [value, setValue] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  useEffect(() => {
    let cancelled = false;
    getAccountSettings()
      .then(
        (settings) =>
          !cancelled &&
          setValue(
            centsToInput(settings.total_reserve_floor_cents, i18n.locale),
          ),
      )
      .catch(
        (reason: unknown) =>
          !cancelled &&
          setStatus({
            kind: "error",
            text: reason instanceof Error ? reason.message : String(reason),
          }),
      );
    return () => {
      cancelled = true;
    };
  }, [i18n.locale]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cents = parseEuroInput(value ?? "");
    if (cents === null || cents < 0) {
      setStatus({
        kind: "error",
        text: t`Enter the reserve as a positive amount or 0.`,
      });
      return;
    }
    try {
      const saved = await putAccountSettings({
        total_reserve_floor_cents: cents,
      });
      setStatus({ kind: "saved", cents: saved.total_reserve_floor_cents });
    } catch (reason) {
      setStatus({
        kind: "error",
        text: reason instanceof Error ? reason.message : String(reason),
      });
    }
  }

  const savedAmount =
    status.kind === "saved" ? formatCents(status.cents, i18n.locale) : "";

  return (
    <form onSubmit={onSubmit}>
      <h2>
        <Trans>Reserve</Trans>
      </h2>
      <p>
        <Trans>
          The amount that should stay available across all accounts together.
          Each account also has its own reserve floor on the Accounts page.
        </Trans>
      </p>
      <div className="accounts-reserve">
        <Field
          htmlFor="settings-total-reserve"
          label={<Trans>Total reserve (€)</Trans>}
        >
          <input
            id="settings-total-reserve"
            inputMode="decimal"
            required
            disabled={value === null}
            value={value ?? ""}
            onChange={(event) => setValue(event.target.value)}
          />
        </Field>
        <Button variant="primary" type="submit" disabled={value === null}>
          <Trans>Save</Trans>
        </Button>
      </div>
      <p role="status">
        {status.kind === "saved" && (
          <Trans>Total reserve saved: {savedAmount}</Trans>
        )}
        {status.kind === "error" && (
          <span className="error">{status.text}</span>
        )}
      </p>
    </form>
  );
}
