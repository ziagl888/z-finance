import type { MessageDescriptor } from "@lingui/core";
import { Trans, useLingui } from "@lingui/react/macro";

import { Badge, Table } from "../../ui";
import { DELIMITER_LABELS } from "./labels";
import { looksGarbled } from "./logic";
import type { Delimiter, Detected } from "./importApi";

interface Props {
  detected: Detected;
  /** Role per header name (from the profile form); marks the mapped columns. */
  roles?: Record<string, string[]>;
}

/**
 * What the engine read: the dialect it used and the first five records, so the user can see
 * whether the columns and the umlauts are right (UC-ZF-005 1a, 2).
 */
export function DetectedTable({ detected, roles = {} }: Props) {
  const { t, i18n } = useLingui();
  const { encoding, rows } = detected;
  // The engine names the delimiter it used; a name this UI does not know is shown as sent.
  const label = DELIMITER_LABELS[detected.delimiter as Delimiter] as
    MessageDescriptor | undefined;
  const separator = label ? i18n._(label) : detected.delimiter;
  const shown = rows.length;
  return (
    <div className="import-detected">
      <p className="ui-hint">
        <Trans>
          Read as {encoding}, separator: {separator}. First {shown} rows:
        </Trans>
      </p>
      {looksGarbled(detected) && (
        <p role="alert" className="error">
          <Trans>
            Umlauts or the euro sign look wrong. Choose the other encoding
            above.
          </Trans>
        </p>
      )}
      <div className="import-scroll">
        <Table aria-label={t`First rows of the file`}>
          <thead>
            <tr>
              {detected.header.map((name, index) => (
                <th key={`${index}-${name}`}>
                  {name}
                  {(roles[name] ?? []).map((role) => (
                    <Badge key={role} tone="planned" className="import-role">
                      {role}
                    </Badge>
                  ))}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {detected.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {detected.header.map((_, index) => (
                  <td key={index}>{row[index] ?? ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </div>
  );
}
