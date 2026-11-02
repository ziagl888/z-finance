import { Trans, useLingui } from "@lingui/react/macro";
import { useId } from "react";
import { Link } from "react-router";

import { Button, Card, Field } from "../../ui";
import type { Account } from "../accounts/accountsApi";
import { DetectedTable } from "./DetectedTable";
import type { Detected } from "./importApi";
import {
  DELIMITER_LABELS,
  DELIMITERS,
  ENCODING_LABELS,
  ENCODINGS,
} from "./labels";
import type { ProfileForm } from "./logic";

interface Props {
  accounts: Account[] | null;
  accountId: string;
  onAccount: (id: string) => void;
  fileName: string | null;
  onFile: (file: File) => void;
  /** The dialect fields of the profile form: this step chooses the encoding and delimiter by hand. */
  form: ProfileForm;
  onForm: (patch: Partial<ProfileForm>) => void;
  detected: Detected | null;
  detectError: string | null;
  detecting: boolean;
  onNext: () => void;
}

/** Step 1 (UC-ZF-005 1, 1a): account and file, what the engine recognised, manual dialect choice. */
export function FileStep({
  accounts,
  accountId,
  onAccount,
  fileName,
  onFile,
  form,
  onForm,
  detected,
  detectError,
  detecting,
  onNext,
}: Props) {
  const { t, i18n } = useLingui();
  const ids = {
    account: useId(),
    file: useId(),
    encoding: useId(),
    delimiter: useId(),
  };
  const noAccounts = accounts !== null && accounts.length === 0;

  if (noAccounts) {
    return (
      <p className="ui-hint">
        <Trans>
          Create an account first: <Link to="/accounts">Accounts</Link>.
        </Trans>
      </p>
    );
  }

  return (
    <Card className="import-step">
      <div className="ui-form-grid import-grid">
        <Field htmlFor={ids.account} label={<Trans>Account</Trans>}>
          <select
            id={ids.account}
            value={accountId}
            onChange={(event) => onAccount(event.target.value)}
          >
            <option value="">{t`Choose…`}</option>
            {(accounts ?? []).map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </Field>
        <Field
          htmlFor={ids.file}
          label={<Trans>CSV file</Trans>}
          hint={fileName ?? <Trans>Export it from your online banking.</Trans>}
        >
          <input
            id={ids.file}
            type="file"
            accept=".csv,.txt,text/csv,text/plain"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onFile(file);
            }}
          />
        </Field>
        <Field htmlFor={ids.encoding} label={<Trans>Encoding</Trans>}>
          <select
            id={ids.encoding}
            value={form.encoding}
            onChange={(event) =>
              onForm({
                encoding: event.target.value as ProfileForm["encoding"],
              })
            }
          >
            {ENCODINGS.map((value) => (
              <option key={value} value={value}>
                {i18n._(ENCODING_LABELS[value])}
              </option>
            ))}
          </select>
        </Field>
        <Field htmlFor={ids.delimiter} label={<Trans>Column separator</Trans>}>
          <select
            id={ids.delimiter}
            value={form.delimiter}
            onChange={(event) =>
              onForm({
                delimiter: event.target.value as ProfileForm["delimiter"],
              })
            }
          >
            {DELIMITERS.map((value) => (
              <option key={value} value={value}>
                {i18n._(DELIMITER_LABELS[value])}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {fileName !== null && detecting && detected === null && (
        <p role="status" className="ui-hint">
          <Trans>Reading the file…</Trans>
        </p>
      )}
      {detectError && (
        <p role="alert" className="error">
          {detectError}
        </p>
      )}
      {detected && <DetectedTable detected={detected} />}
      <div className="ui-actions">
        <Button
          variant="primary"
          disabled={!accountId || detected === null || detectError !== null}
          onClick={onNext}
        >
          <Trans>Continue to the profile</Trans>
        </Button>
      </div>
    </Card>
  );
}
