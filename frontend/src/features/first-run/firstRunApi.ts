import { api } from "../../api/client";
import { parseEuroInput } from "../accounts/accountsApi";
import type { LevySettings, LevySettingsInput } from "../levy/levyApi";

/** Highest levy rate the backend accepts, in basis points (90 %, see /api/levy/settings). */
export const MAX_RATE_BP = 9000;

export const getLevySettings = () =>
  api.get<LevySettings>("/api/levy/settings");
export const putLevySettings = (input: LevySettingsInput) =>
  api.put<LevySettings>("/api/levy/settings", input);

/**
 * Parse a levy rate typed as a percentage ("35", "35,5", "35.5") into basis points.
 *
 * A percentage with two decimals times 100 is exactly basis points, which is what the amount
 * parser already computes for cents — so the same rule applies and no float is involved. Returns
 * null for anything that is not a rate between 0 and 90 %.
 */
export function parseRateInput(text: string): number | null {
  const basisPoints = parseEuroInput(text);
  if (basisPoints === null || basisPoints < 0 || basisPoints > MAX_RATE_BP)
    return null;
  return basisPoints;
}

/** Today as `YYYY-MM-DD` in the local time zone — the opening date the first account starts with. */
export function todayIso(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}
