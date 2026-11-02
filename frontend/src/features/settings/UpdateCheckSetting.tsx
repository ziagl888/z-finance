import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState } from "react";

import { Button, Field } from "../../ui";
import {
  getUpdateCheck,
  putUpdateCheck,
  runUpdateCheck,
  type UpdateCheckState,
} from "./updateCheckApi";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Update notice (UC NF10): switch the check on, ask once, follow the link.
 *
 * Off until the user switches it on — z-finance makes no connection of its own (UC NF2) — and even
 * then it only reads which version is published. Nothing is downloaded or installed here; the link
 * opens the releases page, and the new version is installed like the first one.
 */
export function UpdateCheckSetting() {
  const { t } = useLingui();
  const [state, setState] = useState<UpdateCheckState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getUpdateCheck()
      .then((loaded) => !cancelled && setState(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, []);

  async function change(action: () => Promise<UpdateCheckState>) {
    setBusy(true);
    setError(null);
    try {
      setState(await action());
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="update-check-title">
      <h2 id="update-check-title">
        <Trans>Updates</Trans>
      </h2>
      <p>
        <Trans>
          z-finance can look whether a newer version has been published. It only
          asks GitHub which version that is — it downloads and installs nothing.
          Your data stays where it is when you install a new version.
        </Trans>
      </p>
      <Field
        htmlFor="update-check-enabled"
        label={<Trans>Look for new versions</Trans>}
        hint={<Trans>Off by default. Nothing is sent, only asked.</Trans>}
      >
        <input
          id="update-check-enabled"
          type="checkbox"
          checked={state?.enabled ?? false}
          disabled={state === null || busy}
          onChange={(event) =>
            void change(() => putUpdateCheck(event.target.checked))
          }
        />
      </Field>
      <div className="ui-actions">
        <Button
          type="button"
          disabled={!state?.enabled || busy}
          onClick={() => void change(runUpdateCheck)}
        >
          <Trans>Check now</Trans>
        </Button>
      </div>
      <p role="status">
        {state && <Result state={state} unchecked={t`Not checked yet.`} />}
        {error && <span className="error">{error}</span>}
      </p>
    </section>
  );
}

/** One line: which version runs, and what the last check found (placeholders are identifiers). */
function Result({
  state,
  unchecked,
}: {
  state: UpdateCheckState;
  unchecked: string;
}) {
  const { current_version: current, latest_version: latest } = state;
  if (state.update_available && latest) {
    return (
      <span className="ui-notice">
        <Trans>
          Version {latest} is available; you run {current}.
        </Trans>{" "}
        <a href={state.release_url} target="_blank" rel="noreferrer noopener">
          <Trans>Open the download page</Trans>
        </a>
      </span>
    );
  }
  if (state.checked_at) {
    return <Trans>You run version {current}, and it is the newest one.</Trans>;
  }
  return (
    <>
      <Trans>You run version {current}.</Trans> {state.enabled ? unchecked : ""}
    </>
  );
}
