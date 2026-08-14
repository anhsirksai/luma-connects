"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { getRunStatus, runStreamUrl } from "../../lib/api";
import type { RunEvent } from "../../lib/api-shape";

const KNOWN_EVENT_TYPES = ["phase", "log", "fetch", "cache_hit", "serp", "error", "done"];
const MAX_RECONNECT_ATTEMPTS = 3;
const POLL_FALLBACK_INTERVAL_MS = 2000;
const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

export function RunProgress({
  runId,
  initialPhase,
}: {
  runId: number;
  initialPhase?: string | null;
}) {
  const router = useRouter();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [phase, setPhase] = useState<string | null>(initialPhase ?? null);
  const [status, setStatus] = useState<string>("running");
  const lastSeq = useRef(0);
  const reconnectAttempts = useRef(0);
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    let es: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;

    const finish = (finalStatus: string) => {
      setStatus(finalStatus);
      if (pollTimer) clearInterval(pollTimer);
      es?.close();
      router.refresh();
    };

    const startPollingFallback = () => {
      if (pollTimer) return;
      pollTimer = setInterval(async () => {
        try {
          const run = await getRunStatus(runId);
          if (cancelled) return;
          setPhase(run.phase);
          setEvents(run.events);
          if (TERMINAL_STATUSES.has(run.status)) finish(run.status);
        } catch {
          // Keep retrying on the interval; a transient network blip shouldn't
          // stop polling.
        }
      }, POLL_FALLBACK_INTERVAL_MS);
    };

    const connect = () => {
      es = new EventSource(runStreamUrl(runId, lastSeq.current));

      const handleFrame = (type: string) => (evt: MessageEvent) => {
        reconnectAttempts.current = 0;
        try {
          const payload = JSON.parse(evt.data) as {
            seq: number;
            ts: string;
            message: string;
            data?: Record<string, unknown>;
          };
          lastSeq.current = payload.seq ?? lastSeq.current;
          setEvents((prev) => [
            ...prev,
            { seq: payload.seq, ts: payload.ts, type, message: payload.message, data: payload.data ?? {} },
          ]);
          const framePhase = payload.data?.phase;
          if (type === "phase" && typeof framePhase === "string") setPhase(framePhase);
          if (type === "done") finish("succeeded");
          if (type === "error") finish("failed");
        } catch {
          // Ignore malformed frames rather than breaking the whole stream.
        }
      };

      KNOWN_EVENT_TYPES.forEach((type) => es?.addEventListener(type, handleFrame(type)));

      es.onerror = () => {
        es?.close();
        reconnectAttempts.current += 1;
        if (reconnectAttempts.current > MAX_RECONNECT_ATTEMPTS) {
          startPollingFallback();
          return;
        }
        setTimeout(() => {
          if (!cancelled) connect();
        }, 800 * reconnectAttempts.current);
      };
    };

    connect();

    return () => {
      cancelled = true;
      es?.close();
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [runId, router]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [events]);

  return (
    <div className="rounded-xl border border-[#dbe3ee] bg-white p-5">
      <div className="flex items-center gap-2">
        <span className="relative flex h-2.5 w-2.5">
          {status !== "failed" && (
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#3d7ffc] opacity-60" />
          )}
          <span
            className={`relative inline-flex h-2.5 w-2.5 rounded-full ${status === "failed" ? "bg-[#b3261e]" : "bg-[#3d7ffc]"}`}
          />
        </span>
        <p className="text-sm font-semibold text-[#091b36]">
          {status === "failed" ? "This run hit a problem" : "Analyzing this event..."}
        </p>
      </div>

      {phase && (
        <p className="mt-1 text-xs font-medium uppercase tracking-wide text-[#7c8aa0]">
          {phase.replace(/_/g, " ")}
        </p>
      )}

      <div
        ref={logRef}
        className="mt-4 max-h-64 space-y-1 overflow-y-auto rounded-lg bg-[#f4f7fb] p-3 text-xs text-[#42536b]"
      >
        {events.length === 0 && <p>Waiting for progress...</p>}
        {events.map((entry) => (
          <p key={entry.seq} className={entry.type === "error" ? "text-[#b3261e]" : undefined}>
            {entry.message}
          </p>
        ))}
      </div>
    </div>
  );
}
