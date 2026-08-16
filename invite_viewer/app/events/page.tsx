import { cookies } from "next/headers";
import Link from "next/link";

import { AddEventForm } from "../components/events/AddEventForm";
import { EventCard } from "../components/events/EventCard";
import { EventDateNav } from "../components/events/EventDateNav";
import { LogoutButton } from "../components/ui/LogoutButton";
import { listEvents } from "../lib/api";
import { ApiError } from "../lib/api";

export const dynamic = "force-dynamic";

// Server Components can't read document.cookie, but the browser sends this
// cookie with the page navigation itself, so next/headers picks it up here.
// This file is unambiguously server-only (a page.tsx default export), so
// importing next/headers is safe — see the comment in lib/api.ts for why
// that import must never land in the shared client/server module instead.
async function adminAuthHeader(): Promise<string | undefined> {
  const store = await cookies();
  const token = store.get("luma_admin_token")?.value;
  return token ? `Bearer ${token}` : undefined;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default async function EventsPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const params = await searchParams;
  const date = params.date && /^\d{4}-\d{2}-\d{2}$/.test(params.date) ? params.date : todayIso();
  const from = `${date}T00:00:00`;
  const to = `${date}T23:59:59`;

  const authHeader = await adminAuthHeader();
  let events: Awaited<ReturnType<typeof listEvents>>["events"] = [];
  let loadError: string | null = null;
  let needsLogin = false;
  try {
    const response = await listEvents({ from, to, limit: 100 }, authHeader);
    events = response.events;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      needsLogin = true;
    } else {
      loadError =
        error instanceof ApiError
          ? error.message
          : "Could not load events from the Luma Connects API.";
    }
  }

  return (
    <main className="min-h-screen bg-[#f4f7fb] px-4 py-10 sm:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-[880px] flex-col gap-8">
        <header className="flex items-center justify-between gap-4">
          <Link href="/" className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#3d7ffc] text-sm font-semibold text-white">
              BD
            </span>
            <span className="text-base font-semibold text-[#091b36]">Luma Connects</span>
          </Link>
          {authHeader && <LogoutButton />}
        </header>

        {needsLogin ? (
          <div className="rounded-xl border border-[#dbe3ee] bg-white p-6 text-center">
            <p className="text-sm font-semibold text-[#091b36]">Sign in required</p>
            <p className="mt-1 text-sm text-[#5b6b82]">
              Your session has expired, or this API is passcode-protected.
            </p>
            <Link
              href="/login"
              className="mt-4 inline-flex items-center justify-center rounded-lg bg-[#3d7ffc] px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-[#2f6ee8]"
            >
              Log in
            </Link>
          </div>
        ) : (
          <>
            <div>
              <h1 className="text-3xl font-semibold text-[#091b36]">
                Which events are most relevant to you?
              </h1>
              <p className="mt-2 text-[#5b6b82]">
                Open an event to explore who&apos;s there and who are the most relevant people you
                can meet.
              </p>
            </div>

            <AddEventForm />

            <EventDateNav date={date} eventCount={events.length} />

            <section className="flex flex-col gap-3">
              {loadError && (
                <div className="rounded-xl border border-[#f3c6c2] bg-[#fdf1f0] p-4 text-sm text-[#8a2f27]">
                  {loadError}
                </div>
              )}

              {!loadError && events.length === 0 && (
                <div className="rounded-xl border border-dashed border-[#dbe3ee] bg-white p-8 text-center text-sm text-[#7c8aa0]">
                  No events for this day yet. Paste a Luma link above to analyze one.
                </div>
              )}

              {events.map((event) => (
                <EventCard key={event.id} event={event} />
              ))}
            </section>
          </>
        )}
      </div>
    </main>
  );
}
