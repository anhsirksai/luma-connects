import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "GTM My Events | Bright Data Agent",
  description:
    "Learn how the Bright Data invite agent turns public web signals into event-ready prospect reports and a review dashboard.",
};

const workflowSteps = [
  {
    title: "Describe the event",
    body: "Give the agent an event page, city, audience angle, or target account pattern. It turns that brief into focused public web research tasks.",
  },
  {
    title: "Collect public signals",
    body: "The workflow searches event pages, company pages, professional profiles, and other public sources through Bright Data infrastructure.",
  },
  {
    title: "Score the invite fit",
    body: "Candidates are normalized into report JSON with evidence, relevance rationale, source queries, and a fit score your team can audit.",
  },
  {
    title: "Review in the web app",
    body: "The dashboard lets GTM teams compare runs, filter by account or score, import new reports, and inspect the proof before outreach.",
  },
];

const agentCapabilities = [
  "Turns an event brief into reusable discovery queries.",
  "Structures messy public web findings into a consistent invite report.",
  "Keeps evidence beside every recommended prospect.",
  "Gives field, sales, and community teams a shared review surface.",
];

const exampleSignals = [
  "City and market match",
  "Relevant title or team",
  "Company category fit",
  "Event-topic evidence",
];

export default function Home() {
  return (
    <main className="min-h-screen bg-[#fbfcf8] text-[#101820]">
      <Hero />

      <section
        className="border-y border-[#d7dfd0] bg-[#f2f6ec] px-4 py-14 sm:px-6 lg:px-8"
        id="agent"
      >
        <div className="mx-auto grid w-full max-w-[1180px] gap-8 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] lg:items-start">
          <div>
            <p className="text-sm font-semibold uppercase text-[#1d6d59]">
              Agent workflow
            </p>
            <h2 className="mt-3 max-w-2xl text-3xl font-semibold leading-tight text-[#101820] md:text-5xl">
              Research that stays useful after the crawl is done.
            </h2>
            <p className="mt-5 max-w-2xl text-base leading-7 text-[#4b5a55] md:text-lg">
              The agent is built for event-based GTM work: find the people who
              have a real reason to be in the room, show the public evidence,
              and package the result for a human review pass.
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            {agentCapabilities.map((capability) => (
              <div
                className="rounded-lg border border-[#cfdac6] bg-white p-4 shadow-sm"
                key={capability}
              >
                <span className="block h-1.5 w-10 rounded-full bg-[#3d7ffc]" />
                <p className="mt-4 text-sm leading-6 text-[#33413c]">
                  {capability}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="bg-white px-4 py-14 sm:px-6 lg:px-8" id="how-it-works">
        <div className="mx-auto w-full max-w-[1180px]">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <p className="text-sm font-semibold uppercase text-[#3d7ffc]">
                How it works
              </p>
              <h2 className="mt-3 max-w-2xl text-3xl font-semibold leading-tight md:text-5xl">
                From public web research to a ranked invite list.
              </h2>
            </div>
            <Link
              className="inline-flex w-fit items-center justify-center rounded-lg border border-[#c8d5e3] px-4 py-3 text-sm font-semibold text-[#101820] transition hover:border-[#3d7ffc] hover:text-[#0a3d91]"
              href="/events"
            >
              Browse events
            </Link>
          </div>

          <div className="mt-8 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {workflowSteps.map((step, index) => (
              <article
                className="rounded-lg border border-[#d9e3ef] bg-[#fbfcf8] p-5"
                key={step.title}
              >
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#101820] text-sm font-semibold text-white">
                  {index + 1}
                </span>
                <h3 className="mt-5 text-lg font-semibold">{step.title}</h3>
                <p className="mt-3 text-sm leading-6 text-[#526373]">
                  {step.body}
                </p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="bg-[#101820] px-4 py-14 text-white sm:px-6 lg:px-8">
        <div className="mx-auto grid w-full max-w-[1180px] gap-8 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:items-center">
          <div>
            <p className="text-sm font-semibold uppercase text-[#ffca36]">
              Web app
            </p>
            <h2 className="mt-3 max-w-xl text-3xl font-semibold leading-tight md:text-5xl">
              A review surface for teams that need traceability.
            </h2>
            <p className="mt-5 max-w-xl text-base leading-7 text-white/72">
              The app reads generated report JSON from the project reports
              folder, supports browser imports for new runs, and keeps the
              rationale visible next to every prospect.
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            {exampleSignals.map((signal) => (
              <div
                className="rounded-lg border border-white/12 bg-white/8 p-5"
                key={signal}
              >
                <p className="text-sm font-semibold text-[#8db5ff]">Signal</p>
                <p className="mt-2 text-xl font-semibold">{signal}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}

function Hero() {
  return (
    <section className="relative isolate flex min-h-[82vh] overflow-hidden bg-[#eaf1df] px-4 py-8 sm:px-6 lg:px-8">
      <div className="absolute inset-0 -z-20 bg-[#eaf1df]" />
      <HeroScene />
      <div className="absolute inset-0 -z-10 bg-[#101820]/42" />

      <div className="mx-auto flex w-full max-w-[1180px] flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-white/18 pb-5 text-white">
          <Link className="flex items-center gap-3" href="/">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#3d7ffc] text-sm font-semibold">
              BD
            </span>
            <span className="text-base font-semibold">GTM My Events</span>
          </Link>

          <nav className="flex items-center gap-2 text-sm font-semibold text-white/80">
            <a className="hidden px-3 py-2 hover:text-white sm:inline" href="#agent">
              Agent
            </a>
            <a
              className="hidden px-3 py-2 hover:text-white sm:inline"
              href="#how-it-works"
            >
              Workflow
            </a>
            <Link
              className="rounded-lg bg-white px-4 py-2 text-[#101820] transition hover:bg-[#ffca36]"
              href="/events"
            >
              Browse events
            </Link>
          </nav>
        </header>

        <div className="flex flex-1 items-center py-16">
          <div className="max-w-3xl text-white">
            <p className="text-sm font-semibold uppercase text-[#ffca36]">
              Bright Data invite agent
            </p>
            <h1 className="mt-4 text-5xl font-semibold leading-[1.02] md:text-7xl">
              GTM My Events
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-8 text-white/82 md:text-xl">
              An agent and web app for turning public web signals into
              event-ready invite lists, with every recommendation grounded in
              evidence your team can inspect.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <Link
                className="inline-flex items-center justify-center rounded-lg bg-[#3d7ffc] px-5 py-3 text-sm font-semibold text-white transition hover:bg-[#2f6ee8]"
                href="/events"
              >
                Browse events
              </Link>
              <a
                className="inline-flex items-center justify-center rounded-lg border border-white/30 px-5 py-3 text-sm font-semibold text-white transition hover:border-white hover:bg-white/10"
                href="#how-it-works"
              >
                See how it works
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function HeroScene() {
  return (
    <div
      aria-hidden="true"
      className="absolute inset-0 -z-20 overflow-hidden bg-[#dcebd2]"
    >
      <div className="absolute left-[6%] top-[22%] h-[460px] w-[760px] -rotate-3 rounded-lg border border-[#94b88f] bg-white/88 shadow-2xl shadow-[#101820]/20" />
      <div className="absolute left-[9%] top-[28%] h-[54px] w-[690px] -rotate-3 rounded-t-lg bg-[#101820]" />
      <div className="absolute left-[12%] top-[39%] h-24 w-44 -rotate-3 rounded-lg bg-[#3d7ffc]" />
      <div className="absolute left-[28%] top-[38%] h-28 w-52 -rotate-3 rounded-lg bg-[#ffca36]" />
      <div className="absolute left-[48%] top-[37%] h-28 w-52 -rotate-3 rounded-lg bg-[#48b28a]" />
      <div className="absolute left-[12%] top-[57%] h-3 w-[520px] -rotate-3 rounded-full bg-[#b7c6d6]" />
      <div className="absolute left-[12%] top-[64%] h-3 w-[470px] -rotate-3 rounded-full bg-[#d9e3ef]" />
      <div className="absolute left-[12%] top-[71%] h-3 w-[590px] -rotate-3 rounded-full bg-[#b7c6d6]" />
      <div className="absolute right-[7%] top-[18%] h-[560px] w-[420px] rotate-2 rounded-lg border border-[#94b88f] bg-[#101820] shadow-2xl shadow-[#101820]/25" />
      <div className="absolute right-[10%] top-[26%] h-20 w-72 rotate-2 rounded-lg border border-white/12 bg-white/12" />
      <div className="absolute right-[10%] top-[41%] h-20 w-72 rotate-2 rounded-lg border border-white/12 bg-white/12" />
      <div className="absolute right-[10%] top-[56%] h-20 w-72 rotate-2 rounded-lg border border-white/12 bg-white/12" />
      <div className="absolute right-[17%] top-[73%] h-11 w-48 rotate-2 rounded-lg bg-[#3d7ffc]" />
    </div>
  );
}
