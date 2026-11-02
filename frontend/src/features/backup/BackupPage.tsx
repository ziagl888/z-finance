import { plural } from "@lingui/core/macro";
import { Trans, useLingui } from "@lingui/react/macro";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { Button, Card, Table } from "../../ui";
import {
  createBackup,
  listBackups,
  restoreBackup,
  type RestoreReport,
  type SnapshotList,
} from "./backupApi";
import "./backup.css";

/**
 * What the notice line reports. Success notices keep their data, not their text, so they are
 * translated at render time and follow a language switch; `error` carries the server's message,
 * which the backend already answered in the request language.
 */
type Notice =
  | { kind: "error"; text: string }
  | { kind: "saved"; path: string }
  | { kind: "restored"; report: RestoreReport };

function errorNotice(error: unknown): Notice {
  return {
    kind: "error",
    text: error instanceof Error ? error.message : String(error),
  };
}

/** Back up now, download earlier backups, restore from an export file. */
export function BackupPage() {
  const { t, i18n } = useLingui();
  const [list, setList] = useState<SnapshotList | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);

  const refresh = useCallback(async () => {
    try {
      setList(await listBackups());
    } catch (error) {
      setNotice(errorNotice(error));
    }
  }, []);

  // Initial load: state is set only in the promise callbacks, and not after unmount.
  useEffect(() => {
    let cancelled = false;
    listBackups()
      .then((loaded) => !cancelled && setList(loaded))
      .catch((error: unknown) => !cancelled && setNotice(errorNotice(error)));
    return () => {
      cancelled = true;
    };
  }, []);

  async function onBackup() {
    setBusy(true);
    setNotice(null);
    try {
      const snapshot = await createBackup();
      setNotice({ kind: "saved", path: snapshot.path });
      await refresh();
    } catch (error) {
      setNotice(errorNotice(error));
    } finally {
      setBusy(false);
    }
  }

  async function onRestore(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      setNotice({
        kind: "restored",
        report: await restoreBackup(file),
      });
      await refresh();
    } catch (error) {
      setNotice(errorNotice(error));
    } finally {
      setBusy(false);
    }
  }

  const directory = list?.directory ?? "";

  function noticeText(current: Notice): string {
    if (current.kind === "error") {
      return current.text;
    }
    if (current.kind === "saved") {
      const { path } = current;
      return t`Backup saved to ${path}`;
    }
    const { report } = current;
    const tables = Object.keys(report.rows).length;
    const rows = Object.values(report.rows).reduce(
      (sum, count) => sum + count,
      0,
    );
    const restored = t`Restored ${plural(rows, { one: "# row", other: "# rows" })} in ${plural(tables, { one: "# table", other: "# tables" })}.`;
    const parts = [restored];
    const photos = report.receipt_photos;
    if (photos > 0) {
      parts.push(
        t`Restored ${plural(photos, { one: "# receipt photo", other: "# receipt photos" })}.`,
      );
    }
    if (report.migrations_applied.length > 0) {
      const from = report.export_schema_version;
      const to = report.schema_version;
      parts.push(
        t`The export was migrated from schema version ${from} to ${to}.`,
      );
    }
    return parts.join(" ");
  }

  return (
    <section className="backup">
      <h1>
        <Trans>Backup</Trans>
      </h1>
      {notice && (
        <p
          role={notice.kind === "error" ? "alert" : "status"}
          className={notice.kind === "error" ? "error" : "ui-notice"}
        >
          {noticeText(notice)}
        </p>
      )}

      <Card>
        <h2>
          <Trans>Back up now</Trans>
        </h2>
        <p>
          <Trans>
            Saves all data as a dated JSON export plus a copy of the database
            file, together with the receipt photos. z-finance also backs up
            automatically at every start and keeps the newest 10 of those.
          </Trans>
        </p>
        <Button
          variant="primary"
          type="button"
          onClick={onBackup}
          disabled={busy}
        >
          <Trans>Back up now</Trans>
        </Button>
      </Card>

      <Card>
        <h2>
          <Trans>Existing backups</Trans>
        </h2>
        {list === null ? (
          <p>
            <Trans>Loading…</Trans>
          </p>
        ) : list.snapshots.length === 0 ? (
          <p>
            <Trans>No backups yet.</Trans>
          </p>
        ) : (
          <>
            <p className="ui-hint backup-path">
              <Trans>Folder: {directory}</Trans>
            </p>
            <Table>
              <thead>
                <tr>
                  <th>
                    <Trans>Created</Trans>
                  </th>
                  <th>
                    <Trans>Type</Trans>
                  </th>
                  <th>
                    <Trans>Download</Trans>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.snapshots.map((snapshot) => (
                  <tr key={snapshot.name}>
                    <td>
                      {new Date(snapshot.created_at).toLocaleString(
                        i18n.locale,
                      )}
                    </td>
                    <td>
                      {snapshot.trigger === "auto" ? t`Automatic` : t`Manual`}
                    </td>
                    <td>
                      <a href={snapshot.archive_url} download>
                        <Trans>With receipt photos (.zip)</Trans>
                      </a>{" "}
                      <a href={snapshot.download_url} download>
                        <Trans>export.json only</Trans>
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </>
        )}
      </Card>

      <Card>
        <form onSubmit={onRestore}>
          <h2>
            <Trans>Restore</Trans>
          </h2>
          <p>
            <Trans>
              Loads a backup archive (.zip, with receipt photos) or an
              export.json file into an empty z-finance. An export.json brings
              back the data but no receipt photos. If the database already holds
              data, the restore is refused; nothing is merged.
            </Trans>
          </p>
          <div className="ui-actions">
            <label>
              <Trans>Backup file</Trans>{" "}
              <input
                type="file"
                accept=".zip,.json,application/zip,application/json"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <Button
              variant="primary"
              type="submit"
              disabled={busy || file === null}
            >
              <Trans>Restore</Trans>
            </Button>
          </div>
        </form>
      </Card>
    </section>
  );
}
