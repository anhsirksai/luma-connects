"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApiError, getRunStatus, startRun } from "../../lib/api";

type Phase = "idle" | "submitting" | "waiting" | "error";

const POLL_INTERVAL_MS = 1200;
const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

export function AddEventForm() {
  const router = useRouter();
  const [lumaUrl, setLumaUrl] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, []);

  const pollUntilEventReady = (runId: number) => {
    const tick = async () => {
      try {
        const run = await getRunStatus(runId);
        if (run.event_id !== null) {
          router.push(`/events/${run.event_id}`);
          return;
        }
        if (TERMINAL_STATUSES.has(run.status)) {
          setPhase("error");
          setMessage(run.error || "The run finished without resolving an event.");
          return;
        }
      } catch (error) {
        setPhase("error");
        setMessage(error instanceof ApiError ? error.message : "Lost track of the run.");
        return;
      }
      pollTimer.current = setTimeout(tick, POLL_INTERVAL_MS);
    };
    void tick();
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!lumaUrl.trim()) return;

    setPhase("submitting");
    setMessage(null);
    try {
      const result = await startRun(lumaUrl.trim());
      if (result.event_id !== null && result.status === "ready") {
        router.push(`/events/${result.event_id}`);
        return;
      }
      if (result.run_id !== null) {
        setPhase("waiting");
        setMessage("Fetching the event and finding people who'll be there...");
        pollUntilEventReady(result.run_id);
        return;
      }
      setPhase("error");
      setMessage("Could not start a run for this event.");
    } catch (error) {
      setPhase("error");
      setMessage(
        error instanceof ApiError
          ? error.message
          : "Something went wrong starting this event.",
      );
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-xl border border-[#dbe3ee] bg-white p-4 shadow-sm"
    >
      <label htmlFor="luma-url" className="text-sm font-semibold text-[#091b36]">
        Add an event from Luma
      </label>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <input
          id="luma-url"
          type="url"
          required
          placeholder="https://luma.com/your-event"
          value={lumaUrl}
          onChange={(event) => setLumaUrl(event.target.value)}
          disabled={phase === "submitting" || phase === "waiting"}
          className="flex-1 rounded-lg border border-[#dbe3ee] px-3 py-2.5 text-sm text-[#091b36] outline-none focus:border-[#3d7ffc]"
        />
        <button
          type="submit"
          disabled={phase === "submitting" || phase === "waiting"}
          className="inline-flex items-center justify-center rounded-lg bg-[#3d7ffc] px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-[#2f6ee8] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {phase === "submitting" || phase === "waiting" ? "Working..." : "Analyze event"}
        </button>
      </div>
      {message && (
        <p
          className={`mt-2 text-sm ${phase === "error" ? "text-[#b3261e]" : "text-[#5b6b82]"}`}
        >
          {message}
        </p>
      )}
    </form>
  );
}
