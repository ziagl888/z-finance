import { Trans, useLingui } from "@lingui/react/macro";
import { useState } from "react";

import { isLocale, LOCALES, useLocale } from "../i18n";

/** Language choice in the header; the same setting as on the Settings page. */
export function LanguageSwitch() {
  const { t } = useLingui();
  const { locale, setLocale } = useLocale();
  const [saveFailed, setSaveFailed] = useState(false);

  const onChange = (value: string) => {
    if (!isLocale(value)) return;
    setSaveFailed(false);
    setLocale(value).catch(() => setSaveFailed(true));
  };

  return (
    <div className="shell-lang">
      {saveFailed && (
        <span className="error shell-lang-error">
          <Trans>
            The language could not be saved on the server. It applies in this
            browser only.
          </Trans>
        </span>
      )}
      <select
        aria-label={t`Language`}
        value={locale}
        onChange={(event) => onChange(event.target.value)}
      >
        {Object.entries(LOCALES).map(([code, name]) => (
          <option key={code} value={code} lang={code}>
            {name}
          </option>
        ))}
      </select>
    </div>
  );
}
