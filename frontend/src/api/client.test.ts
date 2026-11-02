// @vitest-environment jsdom
/**
 * API client error handling: FastAPI 422 detail lists become one readable message, and a success
 * answer that is not JSON is an error instead of `undefined`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../i18n/locale";
import { api, ApiError, readResponse } from "./client";

function stubFetch(response: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response()),
  );
}

async function failure(call: Promise<unknown>): Promise<ApiError> {
  const error = await call.then(
    () => undefined,
    (e: unknown) => e,
  );
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

beforeEach(async () => {
  await activateLocale("en");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiError message", () => {
  it("joins field and message of every entry of a 422 detail list", async () => {
    /** Oracle: spec-anchored — FastAPI 422 body shape {detail: [{loc, msg, type}]}; task -013 (1). */
    stubFetch(() =>
      Response.json(
        {
          detail: [
            {
              loc: ["body", "amount_cents"],
              msg: "Input should be a valid integer",
              type: "int_parsing",
            },
            { loc: ["body", "lines", 0, "name"], msg: "Field required" },
          ],
        },
        { status: 422 },
      ),
    );
    const error = await failure(api.post("/api/x", {}));
    expect(error.status).toBe(422);
    expect(error.message).toBe(
      "amount_cents: Input should be a valid integer; lines.0.name: Field required",
    );
    expect(Array.isArray(error.detail)).toBe(true);
  });

  it("uses a bare message when the entry has no field", async () => {
    /** Oracle: spec-anchored — task -013 (1): msg and loc are merged only where a loc exists. */
    stubFetch(() =>
      Response.json(
        { detail: [{ loc: ["body"], msg: "Bad body" }] },
        { status: 422 },
      ),
    );
    expect((await failure(api.get("/api/x"))).message).toBe("Bad body");
  });

  it("keeps a string detail as it is", async () => {
    /** Oracle: spec-anchored — existing behaviour: the backend's string detail is the message. */
    stubFetch(() =>
      Response.json({ detail: "The pot is closed." }, { status: 409 }),
    );
    expect((await failure(api.get("/api/x"))).message).toBe(
      "The pot is closed.",
    );
  });

  it("falls back to the status message for an unusable detail", async () => {
    /** Oracle: spec-anchored — existing fallback text "API request failed with status N". */
    stubFetch(() => Response.json({ detail: [42] }, { status: 422 }));
    expect((await failure(api.get("/api/x"))).message).toBe(
      "API request failed with status 422",
    );
  });

  it("translates the fallback message", async () => {
    /** Oracle: spec-anchored — task -013: the fallback is a Lingui message with a de.po entry. */
    await activateLocale("de");
    stubFetch(() => new Response("oops", { status: 500 }));
    expect((await failure(api.get("/api/x"))).message).toBe(
      "Die API-Anfrage ist mit Status 500 fehlgeschlagen",
    );
  });
});

describe("success answers", () => {
  it("returns the JSON payload", async () => {
    /** Oracle: spec-anchored — unchanged behaviour for a JSON 200. */
    stubFetch(() => Response.json({ a: 1 }));
    expect(await api.get("/api/x")).toEqual({ a: 1 });
  });

  it("accepts 204 as no content", async () => {
    /** Oracle: spec-anchored — HTTP 204 has no body; task -013 (2) keeps it valid. */
    stubFetch(() => new Response(null, { status: 204 }));
    expect(await api.delete("/api/x")).toBeUndefined();
  });

  it("refuses a 200 whose body is not JSON", async () => {
    /** Oracle: spec-anchored — task -013 (2): non-JSON success body throws, not undefined. */
    stubFetch(() => new Response("<html>proxy</html>", { status: 200 }));
    const error = await failure(api.get("/api/x"));
    expect(error.status).toBe(200);
    expect(error.message).toBe(
      "The server sent an answer that is not valid JSON.",
    );
  });

  it("readResponse applies the same rules to a raw fetch response", async () => {
    /** Oracle: spec-anchored — task -013 (1): raw uploads share the client's error logic. */
    const bad = new Response(JSON.stringify({ detail: "No." }), {
      status: 400,
    });
    expect((await failure(readResponse(bad))).message).toBe("No.");
  });
});
