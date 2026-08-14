import Image from "next/image";
import Link from "next/link";

import type { EventSummary } from "../../lib/api-shape";
import { formatEventTimeRange, formatGuestCount, formatVenue } from "../../lib/format";
import { Pill } from "../ui/Pill";
import { CategoryStrip } from "./CategoryStrip";

const STATUS_TONE: Record<string, "confirmed" | "inferred" | "neutral"> = {
  ready: "confirmed",
  running: "inferred",
  pending: "neutral",
  failed: "neutral",
};

const STATUS_LABEL: Record<string, string> = {
  ready: "Ready",
  running: "Analyzing",
  pending: "Pending",
  failed: "Needs retry",
};

export function EventCard({ event }: { event: EventSummary }) {
  const time = formatEventTimeRange(event.start_at, event.end_at, event.timezone);

  return (
    <Link
      href={`/events/${event.id}`}
      className="group flex gap-4 rounded-xl border border-[#dbe3ee] bg-white p-4 shadow-sm transition hover:border-[#3d7ffc] hover:shadow-md sm:items-center"
    >
      <div className="relative h-20 w-28 shrink-0 overflow-hidden rounded-lg bg-[#eef2f8]">
        {event.cover_url ? (
          <Image
            src={event.cover_url}
            alt=""
            fill
            sizes="112px"
            className="object-cover"
            unoptimized
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-xs text-[#94a3b8]">
            No cover
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-3">
          <h3 className="truncate text-base font-semibold text-[#091b36] group-hover:text-[#0a3d91]">
            {event.name}
          </h3>
          <Pill tone={STATUS_TONE[event.status] ?? "neutral"}>
            {STATUS_LABEL[event.status] ?? event.status}
          </Pill>
        </div>

        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm text-[#5b6b82]">
          {time && <span>{time}</span>}
          <span>{formatVenue(event.venue_name, event.city)}</span>
          <span>{formatGuestCount(event.guest_count)}</span>
        </div>

        <div className="mt-3">
          <CategoryStrip bars={event.top_fields} />
        </div>
      </div>

      <div className="hidden shrink-0 text-[#94a3b8] group-hover:text-[#3d7ffc] sm:block" aria-hidden="true">
        &rarr;
      </div>
    </Link>
  );
}
