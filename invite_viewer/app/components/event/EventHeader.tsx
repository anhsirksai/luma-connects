import Image from "next/image";

import type { EventSummary } from "../../lib/api-shape";
import { formatEventDate, formatEventTimeRange, formatGuestCount, formatVenue } from "../../lib/format";

export function EventHeader({ event, sourceUrl }: { event: EventSummary; sourceUrl?: string }) {
  const time = formatEventTimeRange(event.start_at, event.end_at, event.timezone);

  return (
    <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
      <div className="relative h-28 w-40 shrink-0 overflow-hidden rounded-xl bg-[#eef2f8]">
        {event.cover_url ? (
          <Image
            src={event.cover_url}
            alt=""
            fill
            sizes="160px"
            className="object-cover"
            unoptimized
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-xs text-[#94a3b8]">
            No cover
          </div>
        )}
      </div>

      <div className="min-w-0">
        <h1 className="text-2xl font-semibold text-[#091b36] sm:text-3xl">{event.name}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-[#5b6b82]">
          <span>{formatEventDate(event.start_at, event.timezone)}</span>
          {time && <span>{time}</span>}
          <span>{formatVenue(event.venue_name, event.city)}</span>
          <span className="font-medium text-[#091b36]">
            {formatGuestCount(event.guest_count)} registered
          </span>
        </div>
        {sourceUrl && (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noreferrer"
            className="mt-2 inline-block text-sm font-semibold text-[#3d7ffc] hover:underline"
          >
            View on Luma &rarr;
          </a>
        )}
      </div>
    </div>
  );
}
