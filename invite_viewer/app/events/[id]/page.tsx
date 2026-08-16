import { cookies } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";

import { ConnectorChat } from "../../components/event/ConnectorChat";
import { EventHeader } from "../../components/event/EventHeader";
import { RoomSnapshot } from "../../components/event/RoomSnapshot";
import { RunProgress } from "../../components/event/RunProgress";
import { ApiError, getEvent } from "../../lib/api";

export const dynamic = "force-dynamic";

// See the matching helper in app/events/page.tsx for why next/headers is
// safe to import here (a page.tsx default export is always server-only) but
// must never be imported from lib/api.ts, which is shared with the client.
async function adminAuthHeader(): Promise<string | undefined> {
  const store = await cookies();
  const token = store.get("luma_admin_token")?.value;
  return token ? `Bearer ${token}` : undefined;
}

export default async function EventDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const authHeader = await adminAuthHeader();

  let detail;
  try {
    detail = await getEvent(id, authHeader);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    if (error instanceof ApiError && error.status === 401) {
      return (
        <main className="min-h-screen bg-[#f4f7fb] px-4 py-10 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-[880px]">
            <Link href="/events" className="text-sm font-semibold text-[#3d7ffc] hover:underline">
              &larr; All events
            </Link>
            <div className="mt-6 rounded-xl border border-[#dbe3ee] bg-white p-6 text-center">
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
          </div>
        </main>
      );
    }
    return (
      <main className="min-h-screen bg-[#f4f7fb] px-4 py-10 sm:px-6 lg:px-8">
        <div className="mx-auto w-full max-w-[880px]">
          <Link href="/events" className="text-sm font-semibold text-[#3d7ffc] hover:underline">
            &larr; All events
          </Link>
          <div className="mt-6 rounded-xl border border-[#f3c6c2] bg-[#fdf1f0] p-6 text-sm text-[#8a2f27]">
            {error instanceof ApiError
              ? error.message
              : "Could not load this event from the Luma Connects API."}
          </div>
        </div>
      </main>
    );
  }

  const isRunning = detail.event.status === "running" || detail.event.status === "pending";
  const isFailed = detail.event.status === "failed";
  const chatDisabled = detail.counts.total === 0;
  const sourceUrl = detail.event.slug ? `https://luma.com/${detail.event.slug}` : undefined;

  return (
    <main className="min-h-screen bg-[#f4f7fb] px-4 py-8 sm:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-6">
        <Link href="/events" className="text-sm font-semibold text-[#3d7ffc] hover:underline">
          &larr; All events
        </Link>

        <EventHeader event={detail.event} sourceUrl={sourceUrl} />

        {detail.ingest_warnings.length > 0 && (
          <div className="rounded-xl border border-[#f3dfa8] bg-[#fff8e8] p-4 text-sm leading-6 text-[#8a5a00]">
            {detail.ingest_warnings.map((warning) => (
              <p key={warning}>{warning}</p>
            ))}
          </div>
        )}

        {isFailed && detail.last_run?.error && (
          <div className="rounded-xl border border-[#f3c6c2] bg-[#fdf1f0] p-4 text-sm text-[#8a2f27]">
            The last analysis run hit a problem: {detail.last_run.error}
          </div>
        )}

        {isRunning && detail.last_run && (
          <RunProgress runId={detail.last_run.id} initialPhase={detail.last_run.phase} />
        )}

        <div className="grid gap-6 lg:grid-cols-[minmax(320px,0.9fr)_minmax(0,1.6fr)]">
          <div className="lg:order-1">
            <RoomSnapshot snapshot={detail.snapshot} />
          </div>
          <div className="min-h-[560px] lg:order-2">
            <ConnectorChat
              eventId={detail.event.id}
              eventName={detail.event.name}
              disabled={chatDisabled}
            />
          </div>
        </div>
      </div>
    </main>
  );
}
