import { Trans } from "@lingui/react/macro";
import { useState } from "react";

import { isLocale, LOCALES, useLocale } from "../../i18n";
import { Card, Field } from "../../ui";
import { TotalReserveSetting } from "../accounts/TotalReserveSetting";
import { UpdateCheckSetting } from "./UpdateCheckSetting";

/**
 * App settings: the UI language (English default, German), stored in this browser and the
 * backend, the total reserve floor of the accounts feature, and the opt-in update notice.
 */
export function SettingsPage() {
  const { locale, setLocale } = useLocale();
  const [saveFailed, setSaveFailed] = useState(false);

  const onChange = (value: string) => {
    if (!isLocale(value)) return;
    setSaveFailed(false);
    setLocale(value).catch(() => setSaveFailed(true));
  };

  return (
    <section>
      <h1>
        <Trans>Settings</Trans>
      </h1>
      <Card>
        <Field htmlFor="settings-language" label={<Trans>Language</Trans>}>
          <select
            id="settings-language"
            value={locale}
            onChange={(event) => onChange(event.target.value)}
          >
            {Object.entries(LOCALES).map(([code, name]) => (
              <option key={code} value={code} lang={code}>
                {name}
              </option>
            ))}
          </select>
        </Field>
        <p role="status">
          {saveFailed && (
            <span className="error">
              <Trans>
                The language could not be saved on the server. It applies in
                this browser only.
              </Trans>
            </span>
          )}
        </p>
      </Card>
      <Card>
        <TotalReserveSetting />
      </Card>
      <Card>
        <UpdateCheckSetting />
      </Card>
    </section>
  );
}
