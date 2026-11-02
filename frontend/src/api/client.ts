/**
 * Thin JSON client for the z-finance API. All paths are relative ("/api/..."): the backend serves
 * the bundle itself in production, and Vite proxies /api in development.
 *
 * Money fields are integer cents (`*_cents`); format them with `formatCents`, never with floats.
 */
import { i18n } from "@lingui/core";
import { t } from "@lingui/core/macro";

/** One entry of a FastAPI validation error list (`detail` of a 422 response). */
interface ValidationIssue {
  loc?: unknown;
  msg?: unknown;
}

/** Location parts that name the request section, not a field, hence mean nothing to the user. */
const SECTION_PARTS = new Set(["body", "query", "path", "header", "cookie"]);

function describeIssue(issue: unknown): string | undefined {
  if (typeof issue === "string") return issue;
  if (!issue || typeof issue !== "object") return undefined;
  const { loc, msg: text } = issue as ValidationIssue;
  if (typeof text !== "string") return undefined;
  const field = Array.isArray(loc)
    ? loc
        .filter((part) => !SECTION_PARTS.has(String(part)))
        .map(String)
        .join(".")
    : "";
  return field ? `${field}: ${text}` : text;
}

/** The user-facing text for a failed request: string details as they are, 422 lists per field. */
function messageFor(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const lines = detail
      .map(describeIssue)
      .filter((line): line is string => line !== undefined);
    if (lines.length > 0) return lines.join("; ");
  }
  return t`API request failed with status ${status}`;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(messageFor(status, detail));
    this.name = "ApiError";
  }
}

/**
 * Turn a fetch response into its JSON payload. A failure throws `ApiError` with the backend's
 * `detail`; a success without a JSON body (other than 204) throws too, instead of handing
 * `undefined` to a caller that expects data.
 */
export async function readResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  let payload: unknown;
  let isJson = true;
  try {
    payload = await response.json();
  } catch {
    isJson = false;
  }
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? payload.detail
        : payload;
    throw new ApiError(response.status, detail);
  }
  if (!isJson) {
    throw new ApiError(
      response.status,
      t`The server sent an answer that is not valid JSON.`,
    );
  }
  return payload as T;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  // Error messages follow the UI language; a language stored in the settings still wins server-side.
  if (i18n.locale) headers["Accept-Language"] = i18n.locale;
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return readResponse<T>(response);
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
};

/** Response of GET /api/health. */
export interface Health {
  status: string;
  version: string;
  schema_version: number;
}

/** Format integer cents as a euro amount in the given locale (default: browser locale). */
export function formatCents(cents: number, locale?: string): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "EUR",
  }).format(cents / 100);
}
