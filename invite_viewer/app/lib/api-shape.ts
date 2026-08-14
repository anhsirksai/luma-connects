export type SnapshotBar = {
  key: string;
  label: string;
  count: number;
  percentage: number;
};

export type SnapshotSection = {
  id: string;
  title: string;
  bars: SnapshotBar[];
};

export type SnapshotBasis = {
  registered_count: number | null;
  confirmed_people: number;
  inferred_people: number;
  classified_people: number;
  disclaimer: string;
};

export type RoomSnapshot = {
  sections: SnapshotSection[];
  basis: SnapshotBasis;
  generated_at: string;
};

export type EventSummary = {
  id: number;
  slug: string;
  name: string;
  cover_url: string | null;
  start_at: string | null;
  end_at: string | null;
  timezone: string | null;
  venue_name: string | null;
  city: string | null;
  guest_count: number | null;
  people_analyzed: number;
  top_fields: SnapshotBar[];
  status: string;
};

export type EventListResponse = {
  events: EventSummary[];
  total: number;
};

export type RunEvent = {
  seq: number;
  ts: string;
  type: string;
  message: string;
  data: Record<string, unknown>;
};

export type RunStatus = {
  id: number;
  event_id: number | null;
  status: string;
  phase: string | null;
  stats: Record<string, unknown>;
  error: string | null;
  events: RunEvent[];
};

export type EventDetail = {
  event: EventSummary;
  snapshot: RoomSnapshot;
  counts: { total: number; confirmed: number; inferred: number };
  ingest_source: string | null;
  ingest_warnings: string[];
  last_run: RunStatus | null;
};

export type PersonLabels = {
  field: string | null;
  role_type: string | null;
  seniority: string | null;
  industries: string[];
  tags: string[];
};

export type PersonSummary = {
  person_id: number;
  name: string | null;
  headline: string | null;
  company: string | null;
  linkedin_url: string | null;
  avatar_url: string | null;
  is_confirmed_attendee: boolean;
  relation: string;
  labels: PersonLabels;
  relevance_score: number | null;
};

export type PeopleListResponse = {
  people: PersonSummary[];
  total: number;
};

export type PersonCard = PersonSummary & {
  highlight: string;
  why_relevant: string;
  evidence: string[];
};

export type PersonFilter = {
  fields: string[];
  role_types: string[];
  seniorities: string[];
  industries: string[];
  tags_any: string[];
  company_keywords: string[];
  headline_keywords: string[];
  exclude_keywords: string[];
  confirmed_only: boolean;
  limit: number;
  interpretation: string;
};

export type ChatQueryResponse = {
  thread_id: number;
  message_id: number;
  reply: string;
  interpreted_filters: PersonFilter;
  cards: PersonCard[];
  total_matches: number;
  used_fallback: boolean;
  caveats: string[];
};

export type ChatMessage = {
  id: number;
  role: string;
  content: string;
  filters: PersonFilter | null;
  cards: PersonCard[] | null;
  created_at: string;
};

export type ChatMessagesResponse = {
  messages: ChatMessage[];
};

export type CreateEventResponse = {
  run_id: number | null;
  event_id: number | null;
  status: string;
  already_cached: boolean;
};

export type ApiErrorBody = {
  error: { code: string; message: string; detail?: unknown };
};

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asNullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export function normalizeSnapshotBar(value: unknown): SnapshotBar {
  const bar = asRecord(value);
  return {
    key: asString(bar.key),
    label: asString(bar.label) || asString(bar.key),
    count: asNumber(bar.count),
    percentage: asNumber(bar.percentage),
  };
}

function asSnapshotBarArray(value: unknown): SnapshotBar[] {
  return Array.isArray(value) ? value.map(normalizeSnapshotBar) : [];
}

export function normalizeRoomSnapshot(value: unknown): RoomSnapshot {
  const snapshot = asRecord(value);
  const basisRaw = asRecord(snapshot.basis);
  const sections = Array.isArray(snapshot.sections)
    ? snapshot.sections.map((section) => {
        const record = asRecord(section);
        return {
          id: asString(record.id),
          title: asString(record.title),
          bars: asSnapshotBarArray(record.bars),
        };
      })
    : [];

  return {
    sections,
    basis: {
      registered_count: asNullableNumber(basisRaw.registered_count),
      confirmed_people: asNumber(basisRaw.confirmed_people),
      inferred_people: asNumber(basisRaw.inferred_people),
      classified_people: asNumber(basisRaw.classified_people),
      disclaimer: asString(basisRaw.disclaimer),
    },
    generated_at: asString(snapshot.generated_at),
  };
}

export function normalizeEventSummary(value: unknown): EventSummary {
  const event = asRecord(value);
  return {
    id: asNumber(event.id),
    slug: asString(event.slug),
    name: asString(event.name) || "Untitled event",
    cover_url: asNullableString(event.cover_url),
    start_at: asNullableString(event.start_at),
    end_at: asNullableString(event.end_at),
    timezone: asNullableString(event.timezone),
    venue_name: asNullableString(event.venue_name),
    city: asNullableString(event.city),
    guest_count: asNullableNumber(event.guest_count),
    people_analyzed: asNumber(event.people_analyzed),
    top_fields: asSnapshotBarArray(event.top_fields),
    status: asString(event.status) || "pending",
  };
}

export function normalizeEventListResponse(value: unknown): EventListResponse {
  const body = asRecord(value);
  const events = Array.isArray(body.events) ? body.events.map(normalizeEventSummary) : [];
  return { events, total: asNumber(body.total, events.length) };
}

export function normalizeRunEvent(value: unknown): RunEvent {
  const event = asRecord(value);
  return {
    seq: asNumber(event.seq),
    ts: asString(event.ts),
    type: asString(event.type) || "log",
    message: asString(event.message),
    data: asRecord(event.data),
  };
}

export function normalizeRunStatus(value: unknown): RunStatus {
  const run = asRecord(value);
  return {
    id: asNumber(run.id),
    event_id: asNullableNumber(run.event_id),
    status: asString(run.status) || "queued",
    phase: asNullableString(run.phase),
    stats: asRecord(run.stats),
    error: asNullableString(run.error),
    events: Array.isArray(run.events) ? run.events.map(normalizeRunEvent) : [],
  };
}

export function normalizeEventDetail(value: unknown): EventDetail {
  const detail = asRecord(value);
  const countsRaw = asRecord(detail.counts);
  return {
    event: normalizeEventSummary(detail.event),
    snapshot: normalizeRoomSnapshot(detail.snapshot),
    counts: {
      total: asNumber(countsRaw.total),
      confirmed: asNumber(countsRaw.confirmed),
      inferred: asNumber(countsRaw.inferred),
    },
    ingest_source: asNullableString(detail.ingest_source),
    ingest_warnings: asStringArray(detail.ingest_warnings),
    last_run: detail.last_run ? normalizeRunStatus(detail.last_run) : null,
  };
}

export function normalizePersonLabels(value: unknown): PersonLabels {
  const labels = asRecord(value);
  return {
    field: asNullableString(labels.field),
    role_type: asNullableString(labels.role_type),
    seniority: asNullableString(labels.seniority),
    industries: asStringArray(labels.industries),
    tags: asStringArray(labels.tags),
  };
}

export function normalizePersonSummary(value: unknown): PersonSummary {
  const person = asRecord(value);
  return {
    person_id: asNumber(person.person_id),
    name: asNullableString(person.name),
    headline: asNullableString(person.headline),
    company: asNullableString(person.company),
    linkedin_url: asNullableString(person.linkedin_url),
    avatar_url: asNullableString(person.avatar_url),
    is_confirmed_attendee: asBoolean(person.is_confirmed_attendee),
    relation: asString(person.relation) || "inferred",
    labels: normalizePersonLabels(person.labels),
    relevance_score: asNullableNumber(person.relevance_score),
  };
}

export function normalizePeopleListResponse(value: unknown): PeopleListResponse {
  const body = asRecord(value);
  const people = Array.isArray(body.people) ? body.people.map(normalizePersonSummary) : [];
  return { people, total: asNumber(body.total, people.length) };
}

export function normalizePersonCard(value: unknown): PersonCard {
  const person = normalizePersonSummary(value);
  const card = asRecord(value);
  return {
    ...person,
    highlight: asString(card.highlight),
    why_relevant: asString(card.why_relevant),
    evidence: asStringArray(card.evidence),
  };
}

function normalizePersonFilter(value: unknown): PersonFilter {
  const filter = asRecord(value);
  return {
    fields: asStringArray(filter.fields),
    role_types: asStringArray(filter.role_types),
    seniorities: asStringArray(filter.seniorities),
    industries: asStringArray(filter.industries),
    tags_any: asStringArray(filter.tags_any),
    company_keywords: asStringArray(filter.company_keywords),
    headline_keywords: asStringArray(filter.headline_keywords),
    exclude_keywords: asStringArray(filter.exclude_keywords),
    confirmed_only: asBoolean(filter.confirmed_only),
    limit: asNumber(filter.limit, 8),
    interpretation: asString(filter.interpretation),
  };
}

export function normalizeChatQueryResponse(value: unknown): ChatQueryResponse {
  const body = asRecord(value);
  return {
    thread_id: asNumber(body.thread_id),
    message_id: asNumber(body.message_id),
    reply: asString(body.reply),
    interpreted_filters: normalizePersonFilter(body.interpreted_filters),
    cards: Array.isArray(body.cards) ? body.cards.map(normalizePersonCard) : [],
    total_matches: asNumber(body.total_matches),
    used_fallback: asBoolean(body.used_fallback),
    caveats: asStringArray(body.caveats),
  };
}

export function normalizeChatMessage(value: unknown): ChatMessage {
  const message = asRecord(value);
  return {
    id: asNumber(message.id),
    role: asString(message.role) || "assistant",
    content: asString(message.content),
    filters: message.filters ? normalizePersonFilter(message.filters) : null,
    cards: Array.isArray(message.cards) ? message.cards.map(normalizePersonCard) : null,
    created_at: asString(message.created_at),
  };
}

export function normalizeChatMessagesResponse(value: unknown): ChatMessagesResponse {
  const body = asRecord(value);
  return {
    messages: Array.isArray(body.messages) ? body.messages.map(normalizeChatMessage) : [],
  };
}

export function normalizeCreateEventResponse(value: unknown): CreateEventResponse {
  const body = asRecord(value);
  return {
    run_id: asNullableNumber(body.run_id),
    event_id: asNullableNumber(body.event_id),
    status: asString(body.status) || "queued",
    already_cached: asBoolean(body.already_cached),
  };
}
