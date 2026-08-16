"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, requestAdminPasscode, setAdminToken, verifyAdminPasscode } from "../lib/api";

type Phase = "idle" | "verifying" | "requesting" | "error";

export default function LoginPage() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<string | null>(null);

  const handleVerify = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!code.trim()) return;

    setPhase("verifying");
    setMessage(null);
    try {
      const { token, expires_in_hours } = await verifyAdminPasscode(code.trim());
      setAdminToken(token, expires_in_hours);
      router.push("/events");
    } catch (error) {
      setPhase("error");
      // The backend deliberately returns one identical message for wrong,
      // expired, exhausted, and never-issued codes — nothing here narrows it.
      setMessage(error instanceof ApiError ? error.message : "Could not verify that code.");
    }
  };

  const handleRequestCode = async () => {
    setPhase("requesting");
    setMessage(null);
    try {
      const { detail } = await requestAdminPasscode();
      setPhase("idle");
      setMessage(detail);
    } catch (error) {
      setPhase("error");
      setMessage(error instanceof ApiError ? error.message : "Could not send a passcode.");
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f4f7fb] px-4">
      <div className="w-full max-w-sm rounded-xl border border-[#dbe3ee] bg-white p-6 shadow-sm">
        <Link href="/" className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#3d7ffc] text-sm font-semibold text-white">
            BD
          </span>
          <span className="text-base font-semibold text-[#091b36]">Luma Connects</span>
        </Link>

        <h1 className="mt-6 text-xl font-semibold text-[#091b36]">Admin sign-in</h1>
        <p className="mt-1 text-sm text-[#5b6b82]">
          Enter the passcode texted to the operator&apos;s phone.
        </p>

        <form onSubmit={handleVerify} className="mt-5 flex flex-col gap-3">
          <input
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="123456"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            disabled={phase === "verifying"}
            className="rounded-lg border border-[#dbe3ee] px-3 py-2.5 text-center text-lg tracking-[0.3em] text-[#091b36] outline-none focus:border-[#3d7ffc]"
          />
          <button
            type="submit"
            disabled={phase === "verifying" || !code.trim()}
            className="inline-flex items-center justify-center rounded-lg bg-[#3d7ffc] px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-[#2f6ee8] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {phase === "verifying" ? "Checking..." : "Sign in"}
          </button>
        </form>

        <button
          type="button"
          onClick={handleRequestCode}
          disabled={phase === "requesting"}
          className="mt-3 text-sm font-semibold text-[#3d7ffc] hover:underline disabled:cursor-not-allowed disabled:opacity-60"
        >
          {phase === "requesting" ? "Sending..." : "Text me a new code"}
        </button>

        {message && (
          <p
            className={`mt-4 text-sm ${
              phase === "error" ? "text-[#b3261e]" : "text-[#5b6b82]"
            }`}
          >
            {message}
          </p>
        )}
      </div>
    </main>
  );
}
