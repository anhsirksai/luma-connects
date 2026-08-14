import Image from "next/image";

import type { PersonCard as PersonCardType } from "../../lib/api-shape";
import { fieldLabel } from "../../lib/format";
import { Pill } from "../ui/Pill";

export function PersonCard({ person }: { person: PersonCardType }) {
  const initial = (person.name || "?").trim().charAt(0).toUpperCase();

  return (
    <article className="rounded-xl border border-[#dbe3ee] bg-white p-4">
      <div className="flex items-start gap-3">
        <div className="relative h-11 w-11 shrink-0 overflow-hidden rounded-full bg-[#dbe8ff] text-[#0a3d91]">
          {person.avatar_url ? (
            <Image src={person.avatar_url} alt="" fill sizes="44px" className="object-cover" unoptimized />
          ) : (
            <span className="flex h-full w-full items-center justify-center text-sm font-semibold">
              {initial}
            </span>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-[#091b36]">
              {person.name || "Unknown"}
            </h3>
            <Pill tone={person.is_confirmed_attendee ? "confirmed" : "inferred"}>
              {person.is_confirmed_attendee ? "Confirmed guest" : "Likely relevant"}
            </Pill>
          </div>
          {(person.headline || person.company) && (
            <p className="mt-0.5 truncate text-xs text-[#5b6b82]">
              {[person.headline, person.company].filter(Boolean).join(" • ")}
            </p>
          )}
        </div>
      </div>

      <p className="mt-3 text-sm leading-6 text-[#334155]">{person.highlight}</p>
      <p className="mt-1.5 text-xs italic leading-5 text-[#7c8aa0]">{person.why_relevant}</p>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1.5">
          {person.labels.field && <Pill tone="neutral">{fieldLabel(person.labels.field)}</Pill>}
          {person.labels.industries.slice(0, 2).map((industry) => (
            <Pill key={industry} tone="accent">
              {fieldLabel(industry)}
            </Pill>
          ))}
        </div>

        {person.linkedin_url && (
          <a
            href={person.linkedin_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs font-semibold text-[#3d7ffc] hover:underline"
          >
            View LinkedIn &rarr;
          </a>
        )}
      </div>
    </article>
  );
}
