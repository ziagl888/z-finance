import { Trans, useLingui } from "@lingui/react/macro";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  listAccounts,
  listCategories,
  type Account,
  type Category,
} from "../accounts/accountsApi";
import type { Transaction } from "../entry/entryApi";
import { DoneStep } from "./DoneStep";
import { FileStep } from "./FileStep";
import {
  commitImport,
  createProfile,
  detectFile,
  getBooking,
  listProfiles,
  listRuns,
  previewFile,
  readFileBytes,
  updateProfile,
  type Detected,
  type Preview,
  type Profile,
  type Run,
} from "./importApi";
import {
  buildOverrides,
  emptyForm,
  formFromProfile,
  guessColumns,
  isChanged,
  reconcileEdits,
  toProfileInput,
  type Edits,
  type ProfileForm,
} from "./logic";
import { PreviewStep } from "./PreviewStep";
import { ProfileStep, type ProfileChoice } from "./ProfileStep";
import { RunsList } from "./RunsList";
import "./import.css";

type Step = "file" | "profile" | "preview" | "done";
const STEPS: Step[] = ["file", "profile", "preview", "done"];

interface StagedFile {
  name: string;
  bytes: ArrayBuffer;
  /** Counts the picked files, so that the detection of a new file is a new request. */
  token: number;
}

/** The answer of the detection request named by `key` (see `detectKey`). */
interface Settled {
  key: string;
  detected: Detected | null;
  error: string | null;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The ids a preview refers to: matched and candidate planned items, resembled bookings. */
function referencedIds(preview: Preview): number[] {
  const ids = new Set<number>();
  for (const line of preview.lines) {
    if (line.planned_id !== null) ids.add(line.planned_id);
    for (const id of line.candidates) ids.add(id);
    if (line.suspect_id !== null) ids.add(line.suspect_id);
  }
  return [...ids];
}

/**
 * The import assistant (UC-ZF-005): file and account → profile → preview with corrections →
 * protocol. The engine (`/api/imports/*`) classifies and writes; this page walks the user through
 * it and keeps the corrections. Nothing is written before "Apply".
 */
export function ImportPage() {
  const { t } = useLingui();
  const [step, setStep] = useState<Step>("file");
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Step 1 and 2: the file, the profile form (its dialect fields are the step-1 choices).
  const [accountId, setAccountId] = useState("");
  const [file, setFile] = useState<StagedFile | null>(null);
  const [form, setForm] = useState<ProfileForm>(emptyForm);
  const [choice, setChoice] = useState<ProfileChoice>("new");
  const [settled, setSettled] = useState<Settled | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Step 3 and 4.
  const [preview, setPreview] = useState<Preview | null>(null);
  const [edits, setEdits] = useState<Edits>({});
  const [bookings, setBookings] = useState<Record<number, Transaction>>({});
  const [refreshing, setRefreshing] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  // A state update is asynchronous: this flag is what makes a double click send one commit.
  const commitRunning = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const fail = (reason: unknown) => !cancelled && setError(message(reason));
    listAccounts(true)
      .then((loaded) => !cancelled && setAccounts(loaded))
      .catch(fail);
    listCategories()
      .then((loaded) => !cancelled && setCategories(loaded))
      .catch(fail);
    listProfiles()
      .then((loaded) => !cancelled && setProfiles(loaded))
      .catch(fail);
    listRuns()
      .then((loaded) => !cancelled && setRuns(loaded))
      .catch(fail);
    return () => {
      cancelled = true;
    };
  }, []);

  // What the engine reads of the file with the chosen dialect and header lines.
  const skipRows = Number(form.skipRows);
  const skipValid =
    form.skipRows.trim() !== "" &&
    Number.isInteger(skipRows) &&
    skipRows >= 0 &&
    skipRows <= 50;
  const { encoding, delimiter } = form;
  // Names one detection request: another file, dialect or header-line count is another request.
  const detectKey =
    file && skipValid
      ? `${file.token}|${encoding}|${delimiter}|${skipRows}`
      : null;
  useEffect(() => {
    if (!file || detectKey === null) return;
    let cancelled = false;
    detectFile(file.bytes, encoding, delimiter, skipRows)
      .then((result) => {
        if (cancelled) return;
        setSettled({ key: detectKey, detected: result, error: null });
        // A first proposal for an empty form (a stored profile always has its columns).
        setForm((current) =>
          current.date ||
          current.text ||
          current.amount ||
          current.debit ||
          current.credit
            ? current
            : { ...current, ...guessColumns(result.header) },
        );
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setSettled({ key: detectKey, detected: null, error: message(reason) });
      });
    return () => {
      cancelled = true;
    };
  }, [file, detectKey, encoding, delimiter, skipRows]);
  // The last answer stays visible while the next one is on its way; an error only for its own request.
  const detecting = detectKey !== null && settled?.key !== detectKey;
  const detected = settled?.detected ?? null;
  const detectError = settled?.key === detectKey ? settled.error : null;

  const patchForm = useCallback(
    (patch: Partial<ProfileForm>) =>
      setForm((current) => ({ ...current, ...patch })),
    [],
  );

  async function chooseFile(picked: File) {
    try {
      const bytes = await readFileBytes(picked);
      setFile((current) => ({
        name: picked.name,
        bytes,
        token: (current?.token ?? 0) + 1,
      }));
      setSettled(null);
    } catch (reason) {
      setError(message(reason));
    }
  }

  function chooseProfile(next: ProfileChoice) {
    setChoice(next);
    setProfileError(null);
    const stored =
      next === "new" ? undefined : profiles.find((p) => p.id === next);
    if (stored) {
      setForm(formFromProfile(stored));
    } else {
      // A new profile starts from the dialect chosen so far and a proposal for the columns.
      setForm((current) => ({
        ...emptyForm(),
        encoding: current.encoding,
        delimiter: current.delimiter,
        ...(detected ? guessColumns(detected.header) : {}),
      }));
    }
  }

  async function loadBookings(result: Preview) {
    const wanted = referencedIds(result);
    const loaded = await Promise.all(
      wanted.map((id) => getBooking(id).catch(() => null)),
    );
    setBookings((current) => {
      const next = { ...current };
      for (const booking of loaded) if (booking) next[booking.id] = booking;
      return next;
    });
  }

  /** Save a new or changed profile, then classify the file with it (step 2 → 3). */
  async function showPreview() {
    const input = toProfileInput(form);
    if (!input || !file || !accountId || busy) return;
    setBusy(true);
    setProfileError(null);
    try {
      const stored =
        typeof choice === "number"
          ? profiles.find((p) => p.id === choice)
          : undefined;
      let profileId: number;
      if (stored === undefined) profileId = (await createProfile(input)).id;
      else if (isChanged(stored, form))
        profileId = (await updateProfile(stored.id, input)).id;
      else profileId = stored.id;
      setChoice(profileId);
      setProfiles(await listProfiles());
      const result = await previewFile(
        file.bytes,
        profileId,
        Number(accountId),
        file.name,
      );
      setPreview(result);
      setEdits({});
      setCommitError(null);
      setStep("preview");
      void loadBookings(result);
    } catch (reason) {
      // A refused file names the missing column; the profile form is right here to fix it (UC 2a).
      setProfileError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  /** Classify the same file again (after a rule was saved, or when the staged preview is lost); the corrections stay. */
  async function refreshPreview() {
    if (!file || !preview || refreshing) return;
    setRefreshing(true);
    setCommitError(null);
    try {
      const result = await previewFile(
        file.bytes,
        preview.profile_id,
        preview.account_id,
        file.name,
      );
      setPreview(result);
      setEdits((current) => reconcileEdits(result.lines, current));
      void loadBookings(result);
    } catch (reason) {
      setCommitError(message(reason));
    } finally {
      setRefreshing(false);
    }
  }

  async function commit() {
    if (commitRunning.current || !preview) return;
    commitRunning.current = true;
    setCommitting(true);
    setCommitError(null);
    try {
      const result = await commitImport(
        preview.preview_id,
        buildOverrides(preview.lines, edits),
      );
      setRun(result);
      setStep("done");
      listRuns()
        .then(setRuns)
        .catch((reason: unknown) => setError(message(reason)));
    } catch (reason) {
      setCommitError(message(reason));
    } finally {
      commitRunning.current = false;
      setCommitting(false);
    }
  }

  function importAnother() {
    setStep("file");
    setFile(null);
    setSettled(null);
    setPreview(null);
    setEdits({});
    setRun(null);
    setCommitError(null);
    setProfileError(null);
  }

  const stepLabels: Record<Step, string> = {
    file: t`File`,
    profile: t`Profile`,
    preview: t`Preview`,
    done: t`Protocol`,
  };
  const active =
    accounts?.filter((account) => account.archived_at === null) ?? null;

  return (
    <section className="import">
      <h1>
        <Trans>Import bank statement</Trans>
      </h1>
      <ol className="import-steps" aria-label={t`Steps`}>
        {STEPS.map((name, index) => (
          <li key={name} aria-current={name === step ? "step" : undefined}>
            <span className="import-step-number">{index + 1}</span>
            {stepLabels[name]}
          </li>
        ))}
      </ol>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {step === "file" && (
        <>
          <FileStep
            accounts={active}
            accountId={accountId}
            onAccount={setAccountId}
            fileName={file?.name ?? null}
            onFile={(picked) => void chooseFile(picked)}
            form={form}
            onForm={patchForm}
            detected={detected}
            detectError={detectError}
            detecting={detecting}
            onNext={() => setStep("profile")}
          />
          <RunsList runs={runs} accounts={accounts ?? []} />
        </>
      )}
      {step === "profile" && (
        <ProfileStep
          profiles={profiles}
          choice={choice}
          onChoice={chooseProfile}
          form={form}
          onForm={patchForm}
          detected={detected}
          detectError={detectError}
          busy={busy}
          error={profileError}
          onBack={() => setStep("file")}
          onContinue={() => void showPreview()}
        />
      )}
      {step === "preview" && preview && (
        <PreviewStep
          preview={preview}
          edits={edits}
          onEdits={setEdits}
          categories={categories}
          bookings={bookings}
          refreshing={refreshing}
          committing={committing}
          commitError={commitError}
          onBack={() => setStep("profile")}
          onRefresh={() => void refreshPreview()}
          onCommit={() => void commit()}
        />
      )}
      {step === "done" && run && <DoneStep run={run} onAgain={importAnother} />}
    </section>
  );
}
