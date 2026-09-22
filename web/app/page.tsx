import Link from "next/link";
import { ArrowRight, FileSearch, MessageSquareQuote, ShieldCheck, Upload } from "lucide-react";
import { LandingDemo } from "@/components/LandingDemo";
import { Wordmark } from "@/components/ui";

const STEPS = [
  {
    icon: Upload,
    title: "Upload",
    body: "PDF, Word, text or Markdown. Each file is split into passages, and every passage remembers the page it came from — which is what makes a citation possible later.",
  },
  {
    icon: FileSearch,
    title: "Indexed two ways",
    body: "Passages are indexed for meaning and for exact wording. So “how do we get out of this?” finds the termination clause, and “INV-90210” finds the invoice.",
  },
  {
    icon: MessageSquareQuote,
    title: "Ask",
    body: "The model searches your documents itself, and searches again with different words when the first attempt comes back thin. It answers only from what it found.",
  },
  {
    icon: ShieldCheck,
    title: "Verified",
    body: "Every claim carries a quote, and each quote is located in the source before you see it. What is shown is the document’s own wording — not the model’s recollection of it.",
  },
];

export default function Landing() {
  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-30 border-b border-line bg-canvas/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-5">
          <Wordmark />
          <Link
            href="/signin"
            className="rounded-lg px-3 py-1.5 text-sm font-medium text-muted transition-colors hover:bg-sunken hover:text-ink"
          >
            Sign in
          </Link>
        </div>
      </header>

      {/* Hero */}
      <section className="mx-auto max-w-5xl px-5 pb-14 pt-16 sm:pt-24">
        <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-cite-line bg-cite-bg px-3 py-1 text-[11px] font-medium text-cite-fg">
          Every quote checked against the source
        </p>
        <h1 className="max-w-3xl text-balance font-display text-4xl leading-[1.1] tracking-tight sm:text-5xl">
          Ask your documents. Get answers you can verify.
        </h1>
        <p className="mt-5 max-w-2xl text-pretty text-[17px] leading-relaxed text-muted">
          Long documents are slow to search by hand, and a chatbot that has not read yours will
          answer anyway. Verbatim reads only your files, quotes them for every claim, and checks
          each quote against the document before it reaches you.
        </p>

        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Link
            href="/signin"
            className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-canvas transition-opacity hover:opacity-90"
          >
            Open Verbatim
            <ArrowRight className="size-4" />
          </Link>
          <Link
            href="#how"
            className="inline-flex items-center gap-2 rounded-lg border border-line px-4 py-2.5 text-sm font-medium transition-colors hover:bg-sunken"
          >
            How it works
          </Link>
        </div>
      </section>

      {/* The mechanic, running */}
      <section className="mx-auto max-w-5xl px-5 pb-20">
        <LandingDemo />
        <p className="mt-3 text-center text-[12px] text-faint">
          A real answer from two contracts. Click a number to see where it came from.
        </p>
      </section>

      {/* How it works */}
      <section id="how" className="border-y border-line bg-sunken/40">
        <div className="mx-auto max-w-5xl px-5 py-16 sm:py-20">
          <h2 className="font-display text-2xl tracking-tight">How it works</h2>
          <p className="mt-2 max-w-xl text-[15px] leading-relaxed text-muted">
            Retrieval-augmented generation, with one extra step that most implementations skip.
          </p>

          <ol className="mt-10 grid gap-x-10 gap-y-9 sm:grid-cols-2">
            {STEPS.map((step, i) => (
              <li key={step.title} className="flex gap-4">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-line bg-surface text-muted">
                  <step.icon className="size-4" />
                </span>
                <div>
                  <h3 className="font-display text-base tracking-tight">
                    <span className="mr-2 text-faint">{i + 1}</span>
                    {step.title}
                  </h3>
                  <p className="mt-1.5 text-[14px] leading-relaxed text-muted">{step.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Abstention */}
      <section className="mx-auto max-w-5xl px-5 py-16 sm:py-20">
        <div className="grid items-center gap-10 md:grid-cols-2">
          <div>
            <h2 className="font-display text-2xl tracking-tight">
              And when it isn&rsquo;t in there, it says so
            </h2>
            <p className="mt-3 text-[15px] leading-relaxed text-muted">
              The failure that costs you is not a wrong answer — it is a confident one. If your
              documents do not cover the question, Verbatim tells you that instead of reaching for
              something plausible, and cites nothing rather than citing loosely.
            </p>
            <p className="mt-3 text-[15px] leading-relaxed text-muted">
              A passage about a different contract is not an answer about yours, either.
            </p>
          </div>

          <figure className="rounded-2xl border border-line bg-surface p-5">
            <p className="mb-4 inline-block rounded-2xl rounded-br-md bg-sunken px-3.5 py-2 text-[14px]">
              What is the pricing?
            </p>
            <p className="text-[15px] leading-[1.75]">
              I couldn&rsquo;t find this in your documents. The closest passages cover the
              termination fee position and the invoicing cadence, but neither states a price.
            </p>
            <figcaption className="mt-3 text-[11px] text-faint">
              No grounded answer found — nothing cited.
            </figcaption>
          </figure>
        </div>
      </section>

      {/* Close */}
      <section className="border-t border-line">
        <div className="mx-auto max-w-5xl px-5 py-16 text-center sm:py-20">
          <h2 className="font-display text-2xl tracking-tight">Start with one document</h2>
          <p className="mx-auto mt-3 max-w-md text-[15px] leading-relaxed text-muted">
            Put a contract, a handbook or a statute into a Space and ask it something you would
            otherwise have to go and look up.
          </p>
          <Link
            href="/signin"
            className="mt-7 inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-canvas transition-opacity hover:opacity-90"
          >
            Open Verbatim
            <ArrowRight className="size-4" />
          </Link>
        </div>
      </section>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-5xl flex-col items-center justify-between gap-2 px-5 py-8 text-[12px] text-faint sm:flex-row">
          <Wordmark className="text-[13px]" />
          <p>Answers grounded in your documents, with the receipts.</p>
        </div>
      </footer>
    </div>
  );
}
