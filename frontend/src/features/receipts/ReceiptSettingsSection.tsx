import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState, type FormEvent } from "react";

import type { Account, Area } from "../accounts/accountsApi";
import { Button, Field } from "../../ui";
import { MAPPING_LABELS } from "./labels";
import {
  getReceiptSettings,
  putReceiptSettings,
  type AccountMapping,
} from "./receiptsApi";

type Method = "cash" | "card";
const AREAS: Area[] = ["private", "business"];
const METHODS: Method[] = ["cash", "card"];

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * How the API books a receipt: the confidence below which a receipt waits for a check, and the
 * account per area and payment method ("automatic" = the only cash account of the area for cash,
 * the only card account, else the only current account, for card).
 */
export function ReceiptSettingsSection({ accounts }: { accounts: Account[] }) {
  const { t, i18n } = useLingui();
  const [threshold, setThreshold] = useState("");
  const [mapping, setMapping] = useState<AccountMapping | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getReceiptSettings()
      .then((value) => {
        if (cancelled) return;
        setThreshold(String(Math.round(value.confidence_threshold * 100)));
        setMapping(value.accounts);
      })
      .catch((reason) => !cancelled && setStatus(message(reason)));
    return () => {
      cancelled = true;
    };
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const percent = Number(threshold);
    if (
      !mapping ||
      threshold.trim() === "" ||
      !(percent >= 0 && percent <= 100)
    ) {
      setStatus(t`Enter a percentage between 0 and 100.`);
      return;
    }
    try {
      const saved = await putReceiptSettings({
        confidence_threshold: percent / 100,
        accounts: mapping,
      });
      setMapping(saved.accounts);
      setStatus(t`Settings saved.`);
    } catch (reason) {
      setStatus(message(reason));
    }
  }

  return (
    <form className="ui-card rcp-settings" onSubmit={save} noValidate>
      <h2>
        <Trans>Settings</Trans>
      </h2>
      <div className="ui-form-grid">
        <Field
          htmlFor="rcp-threshold"
          label={<Trans>Check receipts read with less than (%)</Trans>}
        >
          <input
            id="rcp-threshold"
            inputMode="numeric"
            value={threshold}
            onChange={(event) => setThreshold(event.target.value)}
          />
        </Field>
        {mapping &&
          AREAS.flatMap((area) =>
            METHODS.map((method) => {
              const id = `rcp-account-${area}-${method}`;
              return (
                <Field
                  key={id}
                  htmlFor={id}
                  label={i18n._(MAPPING_LABELS[area][method])}
                >
                  <select
                    id={id}
                    value={mapping[area][method] ?? ""}
                    onChange={(event) =>
                      setMapping({
                        ...mapping,
                        [area]: {
                          ...mapping[area],
                          [method]: event.target.value
                            ? Number(event.target.value)
                            : null,
                        },
                      })
                    }
                  >
                    <option value="">{t`Automatic`}</option>
                    {accounts
                      .filter((account) => account.area === area)
                      .map((account) => (
                        <option key={account.id} value={account.id}>
                          {account.name}
                        </option>
                      ))}
                  </select>
                </Field>
              );
            }),
          )}
      </div>
      <div className="ui-actions">
        <Button variant="primary" type="submit">
          <Trans>Save settings</Trans>
        </Button>
        {status && <span role="status">{status}</span>}
      </div>
    </form>
  );
}
