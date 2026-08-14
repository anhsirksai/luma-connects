export function formatEventDate(startAt: string | null, timezone: string | null): string {
  if (!startAt) return "Date TBA";
  const date = new Date(startAt);
  if (Number.isNaN(date.getTime())) return "Date TBA";
  try {
    return new Intl.DateTimeFormat("en-US", {
      weekday: "long",
      month: "long",
      day: "numeric",
      year: "numeric",
      timeZone: timezone || undefined,
    }).format(date);
  } catch {
    return new Intl.DateTimeFormat("en-US", {
      weekday: "long",
      month: "long",
      day: "numeric",
      year: "numeric",
    }).format(date);
  }
}

export function formatEventTimeRange(
  startAt: string | null,
  endAt: string | null,
  timezone: string | null,
): string {
  if (!startAt) return "";
  const start = new Date(startAt);
  if (Number.isNaN(start.getTime())) return "";

  const timeFormatter = (date: Date) => {
    try {
      return new Intl.DateTimeFormat("en-US", {
        hour: "numeric",
        minute: "2-digit",
        timeZone: timezone || undefined,
      }).format(date);
    } catch {
      return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(
        date,
      );
    }
  };

  const startLabel = timeFormatter(start);
  if (!endAt) return startLabel;
  const end = new Date(endAt);
  if (Number.isNaN(end.getTime())) return startLabel;
  return `${startLabel} - ${timeFormatter(end)}`;
}

export function formatVenue(venueName: string | null, city: string | null): string {
  const parts = [venueName, city].filter((part): part is string => Boolean(part && part.trim()));
  return parts.length > 0 ? parts.join(", ") : "Location TBA";
}

export function formatGuestCount(guestCount: number | null): string {
  if (guestCount === null) return "Registration count unknown";
  return `~${guestCount.toLocaleString()} people`;
}

export function fieldLabel(value: string | null): string {
  if (!value) return "Unclassified";
  return value
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ")
    .replace(/\bMl\b/, "ML")
    .replace(/\bGtm\b/, "GTM")
    .replace(/\bVc\b/, "VC");
}
