import {
  type ChatMessagesResponse,
  type ChatQueryResponse,
  type CreateEventResponse,
  type EventDetail,
  type EventListResponse,
  type PeopleListResponse,
  type RunStatus,
  normalizeChatMessagesResponse,
  normalizeChatQueryResponse,
  normalizeCreateEventResponse,
  normalizeEventDetail,
  normalizeEventListResponse,
  normalizePeopleListResponse,
  normalizeRunStatus,
} from "./api-shape";

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// Admin session token, when the backend's passcode gate (ADMIN_PHONE) is on.
//
// This module is imported by both Server Components (app/events/page.tsx)
// and Client Components (AddEventForm, RunProgress, ConnectorChat), so it
// must never import `next/headers` at the top level — that throws a build
// error the moment client-bundled code touches it. Server Components read
// the cookie themselves (via `next/headers`, which only Server Component
// files import) and pass it in explicitly; everything client-side reads it
// here directly from `document.cookie`, since a browser tab has no other way
// to persist it across the direct-to-backend fetches these components make.
const ADMIN_TOKEN_COOKIE = "luma_admin_token";

function getAdminTokenFromBrowser(): string | undefined {
  if (typeof document === "undefined") return undefined;
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${ADMIN_TOKEN_COOKIE}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]) : undefined;
}

/** Called from the login page after a passcode is exchanged for a token. */
export function setAdminToken(token: string, expiresInHours: number): void {
  document.cookie = `${ADMIN_TOKEN_COOKIE}=${encodeURIComponent(token)}; path=/; max-age=${
    expiresInHours * 3600
  }; samesite=lax`;
}

export function clearAdminToken(): void {
  document.cookie = `${ADMIN_TOKEN_COOKIE}=; path=/; max-age=0`;
}

export function hasAdminToken(): boolean {
  return Boolean(getAdminTokenFromBrowser());
}

function apiBase(): string {
  if (typeof window === "undefined") {
    return process.env.INVITE_API_BASE_URL || "http://localhost:8000";
  }
  return process.env.NEXT_PUBLIC_INVITE_API_BASE_URL || "http://localhost:8000";
}

async function apiFetch(path: string, init?: RequestInit): Promise<unknown> {
  const url = `${apiBase()}${path}`;
  const browserToken = getAdminTokenFromBrowser();
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      cache: "no-store",
      headers: {
        "content-type": "application/json",
        ...(browserToken ? { Authorization: `Bearer ${browserToken}` } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(
      0,
      "network_error",
      `Could not reach the Luma Connects API at ${apiBase()}. Is the backend running?`,
    );
  }

  if (response.status === 401 && browserToken) {
    // The token we sent is dead (expired, revoked, or the backend restarted
    // its session store). Clearing it now means the next page load shows
    // the "please log in" prompt instead of silently resending a token that
    // can only ever fail.
    clearAdminToken();
  }

  if (!response.ok) {
    let code = "unknown_error";
    let message = `Request to ${path} failed with status ${response.status}.`;
    try {
      const body = (await response.json()) as { error?: { code?: string; message?: string } };
      if (body?.error?.code) code = body.error.code;
      if (body?.error?.message) message = body.error.message;
    } catch {
      // Non-JSON error body; fall back to the generic message above.
    }
    throw new ApiError(response.status, code, message);
  }

  if (response.status === 204) return null;
  return response.json();
}

export async function listEvents(
  params?: {
    from?: string;
    to?: string;
    limit?: number;
    offset?: number;
  },
  // Server Components have no way to read document.cookie, so the two page
  // files that call this server-side (app/events/page.tsx) resolve the
  // admin token themselves via next/headers and pass it through here. Client
  // callers can omit this — apiFetch already attaches the browser's cookie.
  authHeader?: string,
): Promise<EventListResponse> {
  const query = new URLSearchParams();
  if (params?.from) query.set("from", params.from);
  if (params?.to) query.set("to", params.to);
  if (params?.limit) query.set("limit", String(params.limit));
  if (params?.offset) query.set("offset", String(params.offset));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return normalizeEventListResponse(
    await apiFetch(
      `/api/events${suffix}`,
      authHeader ? { headers: { Authorization: authHeader } } : undefined,
    ),
  );
}

export async function getEvent(
  eventId: number | string,
  authHeader?: string,
): Promise<EventDetail> {
  return normalizeEventDetail(
    await apiFetch(
      `/api/events/${eventId}`,
      authHeader ? { headers: { Authorization: authHeader } } : undefined,
    ),
  );
}

export async function getPeople(
  eventId: number | string,
  params?: { field?: string; industry?: string; confirmed?: boolean; q?: string; limit?: number },
): Promise<PeopleListResponse> {
  const query = new URLSearchParams();
  if (params?.field) query.set("field", params.field);
  if (params?.industry) query.set("industry", params.industry);
  if (params?.confirmed !== undefined) query.set("confirmed", String(params.confirmed));
  if (params?.q) query.set("q", params.q);
  if (params?.limit) query.set("limit", String(params.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return normalizePeopleListResponse(await apiFetch(`/api/events/${eventId}/people${suffix}`));
}

export async function startRun(
  lumaUrl: string,
  options?: { forceRefresh?: boolean; maxProfiles?: number },
): Promise<CreateEventResponse> {
  return normalizeCreateEventResponse(
    await apiFetch("/api/events", {
      method: "POST",
      body: JSON.stringify({
        luma_url: lumaUrl,
        force_refresh: options?.forceRefresh ?? false,
        max_profiles: options?.maxProfiles ?? 20,
      }),
    }),
  );
}

export async function postChat(
  eventId: number | string,
  message: string,
  threadId?: number | null,
): Promise<ChatQueryResponse> {
  return normalizeChatQueryResponse(
    await apiFetch(`/api/events/${eventId}/chat`, {
      method: "POST",
      body: JSON.stringify({ message, thread_id: threadId ?? null }),
    }),
  );
}

export async function getChatThread(
  eventId: number | string,
  threadId: number,
): Promise<ChatMessagesResponse> {
  return normalizeChatMessagesResponse(
    await apiFetch(`/api/events/${eventId}/chat/${threadId}`),
  );
}

export async function getRunStatus(runId: number): Promise<RunStatus> {
  return normalizeRunStatus(await apiFetch(`/api/runs/${runId}`));
}

export function runStreamUrl(runId: number, afterSeq = 0): string {
  // EventSource cannot set custom headers, so this is the one call site that
  // sends the admin token in a query param instead — the backend's
  // require_admin_stream deliberately accepts that, and only for this route.
  const token = getAdminTokenFromBrowser();
  const tokenSuffix = token ? `&token=${encodeURIComponent(token)}` : "";
  return `${apiBase()}/api/runs/${runId}/stream?after_seq=${afterSeq}${tokenSuffix}`;
}

// --- admin passcode login -----------------------------------------------

export async function requestAdminPasscode(): Promise<{ sent: boolean; detail: string }> {
  return (await apiFetch("/api/auth/request-code", { method: "POST" })) as {
    sent: boolean;
    detail: string;
  };
}

export async function verifyAdminPasscode(
  code: string,
): Promise<{ token: string; expires_in_hours: number }> {
  return (await apiFetch("/api/auth/verify", {
    method: "POST",
    body: JSON.stringify({ code }),
  })) as { token: string; expires_in_hours: number };
}

export async function logoutAdmin(): Promise<void> {
  const token = getAdminTokenFromBrowser();
  clearAdminToken();
  if (!token) return;
  try {
    // Best-effort: revoke server-side too, so the token can't be replayed if
    // it leaked somewhere before the cookie was cleared. A failure here must
    // not block logout — the cookie is already gone either way.
    await apiFetch("/api/auth/logout", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    // Already logged out client-side; nothing more to do.
  }
}
