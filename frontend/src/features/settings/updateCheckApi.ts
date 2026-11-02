import { api } from "../../api/client";

/** Response of every `/api/update-check` route: the switch plus the last answer from GitHub. */
export interface UpdateCheckState {
  enabled: boolean;
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  /** Page to open to fetch a new version by hand; z-finance never downloads anything itself. */
  release_url: string;
  checked_at: string | null;
}

export const getUpdateCheck = () =>
  api.get<UpdateCheckState>("/api/update-check");
export const putUpdateCheck = (enabled: boolean) =>
  api.put<UpdateCheckState>("/api/update-check", { enabled });
export const runUpdateCheck = () =>
  api.post<UpdateCheckState>("/api/update-check/run");
