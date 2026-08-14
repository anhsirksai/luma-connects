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

function apiBase(): string {
  if (typeof window === "undefined") {
    return process.env.INVITE_API_BASE_URL || "http://localhost:8000";
  }
  return process.env.NEXT_PUBLIC_INVITE_API_BASE_URL || "http://localhost:8000";
}

async function apiFetch(path: string, init?: RequestInit): Promise<unknown> {
  const url = `${apiBase()}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      cache: "no-store",
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(
      0,
      "network_error",
      `Could not reach the Luma Connects API at ${apiBase()}. Is the backend running?`,
    );
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

export async function listEvents(params?: {
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}): Promise<EventListResponse> {
  const query = new URLSearchParams();
  if (params?.from) query.set("from", params.from);
  if (params?.to) query.set("to", params.to);
  if (params?.limit) query.set("limit", String(params.limit));
  if (params?.offset) query.set("offset", String(params.offset));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return normalizeEventListResponse(await apiFetch(`/api/events${suffix}`));
}

export async function getEvent(eventId: number | string): Promise<EventDetail> {
  return normalizeEventDetail(await apiFetch(`/api/events/${eventId}`));
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
  return `${apiBase()}/api/runs/${runId}/stream?after_seq=${afterSeq}`;
}
