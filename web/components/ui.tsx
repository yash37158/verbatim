import type { ComponentProps, ReactNode } from "react";
import type { DocStatus } from "@/lib/types";

export const cn = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("font-display text-[1.05rem] tracking-tight", className)}>
      Verbatim<span className="text-cite-fg">.</span>
    </span>
  );
}

type ButtonProps = ComponentProps<"button"> & { variant?: "primary" | "ghost" | "outline" };

export function Button({ variant = "outline", className, ...props }: ButtonProps) {
  const base =
    "inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors disabled:opacity-40 disabled:pointer-events-none";
  const variants = {
    primary: "bg-accent text-canvas hover:opacity-90",
    outline: "border border-line bg-surface hover:bg-sunken",
    ghost: "hover:bg-sunken text-muted hover:text-ink",
  };
  return <button className={cn(base, variants[variant], className)} {...props} />;
}

const STATUS: Record<DocStatus, { label: string; tone: string }> = {
  queued: { label: "Queued", tone: "text-faint" },
  parsing: { label: "Parsing", tone: "text-warn" },
  embedding: { label: "Embedding", tone: "text-warn" },
  ready: { label: "Ready", tone: "text-good" },
  failed: { label: "Failed", tone: "text-bad" },
};

export function StatusPill({ status }: { status: DocStatus }) {
  const { label, tone } = STATUS[status];
  const busy = status === "parsing" || status === "embedding" || status === "queued";
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[11px] font-medium", tone)}>
      <span className={cn("size-1.5 rounded-full bg-current", busy && "animate-pulse")} />
      {label}
    </span>
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <div className="grid size-11 place-items-center rounded-xl border border-line bg-surface text-faint">{icon}</div>
      <p className="font-display text-lg">{title}</p>
      {children && <div className="max-w-xs text-sm text-muted">{children}</div>}
    </div>
  );
}

export const formatBytes = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

export function formatSoon(iso: string) {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return null;
  const minutes = Math.round((at - Date.now()) / 60_000);
  const clock = new Date(at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (minutes <= 0) return "any moment now";
  if (minutes < 60) return `${clock} (${minutes} min)`;
  return clock;
}

export function formatWhen(iso: string | null) {
  if (!iso) return "No activity yet";
  const days = Math.floor((Date.now() - Date.parse(iso)) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 30) return `${days} days ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
