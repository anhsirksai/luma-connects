"use client";

import { useState } from "react";

import { ApiError, postChat } from "../../lib/api";
import type { PersonCard } from "../../lib/api-shape";
import { PersonCardList } from "./PersonCardList";

type ChatTurn = {
  id: string;
  role: "user" | "assistant";
  content: string;
  cards?: PersonCard[];
  caveats?: string[];
};

const SUGGESTED_PROMPTS = [
  "Show me potential VCs",
  "Who's from big pharma?",
  "Show me founders in the room",
  "Anyone in AI infrastructure?",
];

export function ConnectorChat({
  eventId,
  eventName,
  disabled,
}: {
  eventId: number;
  eventName: string;
  disabled?: boolean;
}) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [threadId, setThreadId] = useState<number | null>(null);
  const [isSending, setIsSending] = useState(false);

  const send = async (message: string) => {
    const trimmed = message.trim();
    if (!trimmed || isSending || disabled) return;

    const userTurn: ChatTurn = { id: `u-${Date.now()}`, role: "user", content: trimmed };
    setTurns((prev) => [...prev, userTurn]);
    setInput("");
    setIsSending(true);

    try {
      const response = await postChat(eventId, trimmed, threadId);
      setThreadId(response.thread_id);
      setTurns((prev) => [
        ...prev,
        {
          id: `a-${response.message_id}`,
          role: "assistant",
          content: response.reply,
          cards: response.cards,
          caveats: response.caveats,
        },
      ]);
    } catch (error) {
      setTurns((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          role: "assistant",
          content:
            error instanceof ApiError
              ? error.message
              : "Something went wrong answering that. Try again.",
        },
      ]);
    } finally {
      setIsSending(false);
    }
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    void send(input);
  };

  return (
    <div className="flex h-full flex-col rounded-xl border border-[#dbe3ee] bg-white">
      <div className="rounded-t-xl bg-[#3d7ffc] px-5 py-4 text-white">
        <p className="text-sm font-semibold">Your Event Super Connector</p>
        <p className="text-xs text-white/80">
          {eventName} &middot; ask who you should meet
        </p>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        <ChatBubble>
          Hi there, I&apos;m your Super Connector, here to help you find and meet the most
          relevant people at this event.
        </ChatBubble>
        <ChatBubble>
          {disabled
            ? "I'm still analyzing this event -- check back in a moment."
            : "What kind of people would you like to meet? (e.g., potential customers, partners, investors, or hires)"}
        </ChatBubble>

        {!disabled && turns.length === 0 && (
          <div className="flex flex-wrap gap-2">
            {SUGGESTED_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => void send(prompt)}
                className="rounded-full border border-[#dbe3ee] bg-[#f4f7fb] px-3 py-1.5 text-xs font-medium text-[#334155] transition hover:border-[#3d7ffc] hover:text-[#3d7ffc]"
              >
                {prompt}
              </button>
            ))}
          </div>
        )}

        {turns.map((turn) =>
          turn.role === "user" ? (
            <div key={turn.id} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-[#091b36] px-4 py-2.5 text-sm text-white">
                {turn.content}
              </div>
            </div>
          ) : (
            <div key={turn.id} className="space-y-3">
              <ChatBubble>{turn.content}</ChatBubble>
              {turn.cards && turn.cards.length > 0 && <PersonCardList cards={turn.cards} />}
              {turn.cards && turn.cards.length === 0 && turn.caveats && turn.caveats.length > 0 && (
                <p className="text-xs text-[#7c8aa0]">{turn.caveats[0]}</p>
              )}
            </div>
          ),
        )}

        {isSending && <ChatBubble>Looking through the room...</ChatBubble>}
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2 border-t border-[#eef2f8] p-3">
        <input
          type="text"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          disabled={disabled || isSending}
          placeholder={disabled ? "Analysis still in progress..." : "Ask who you should meet..."}
          className="flex-1 rounded-lg border border-[#dbe3ee] px-3 py-2.5 text-sm text-[#091b36] outline-none focus:border-[#3d7ffc] disabled:bg-[#f4f7fb]"
        />
        <button
          type="submit"
          disabled={disabled || isSending || !input.trim()}
          className="rounded-lg bg-[#3d7ffc] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[#2f6ee8] disabled:cursor-not-allowed disabled:opacity-60"
        >
          Send
        </button>
      </form>
    </div>
  );
}

function ChatBubble({ children }: { children: React.ReactNode }) {
  return (
    <div className="max-w-[90%] rounded-2xl rounded-tl-sm border border-[#eef2f8] bg-[#f4f7fb] px-4 py-2.5 text-sm leading-6 text-[#334155]">
      {children}
    </div>
  );
}
