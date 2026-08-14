"use client";

import { useRouter } from "next/navigation";

function shiftDate(dateStr: string, days: number): string {
  const date = new Date(`${dateStr}T00:00:00`);
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

function formatLongDate(dateStr: string): string {
  const date = new Date(`${dateStr}T00:00:00`);
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

export function EventDateNav({ date, eventCount }: { date: string; eventCount: number }) {
  const router = useRouter();

  const goTo = (nextDate: string) => {
    router.push(`/events?date=${nextDate}`);
  };

  return (
    <div className="flex items-center gap-4">
      <button
        type="button"
        onClick={() => goTo(shiftDate(date, -1))}
        className="flex h-10 w-10 items-center justify-center rounded-lg border border-[#dbe3ee] bg-white text-[#334155] transition hover:border-[#3d7ffc] hover:text-[#3d7ffc]"
        aria-label="Previous day"
      >
        &larr;
      </button>

      <div>
        <p className="text-lg font-semibold text-[#091b36]">{formatLongDate(date)}</p>
        <p className="text-sm text-[#7c8aa0]">
          {eventCount} {eventCount === 1 ? "event" : "events"}
        </p>
      </div>

      <button
        type="button"
        onClick={() => goTo(shiftDate(date, 1))}
        className="flex h-10 w-10 items-center justify-center rounded-lg border border-[#dbe3ee] bg-white text-[#334155] transition hover:border-[#3d7ffc] hover:text-[#3d7ffc]"
        aria-label="Next day"
      >
        &rarr;
      </button>
    </div>
  );
}
