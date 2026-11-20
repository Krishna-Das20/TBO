import type { InventoryStatus, Severity } from "@/lib/types";

export function Card({ title, action, children, className = "" }: {
  title?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-2xl border border-line bg-card p-5 ${className}`}>
      {(title || action) && (
        <div className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="font-display text-base font-semibold">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function PageTitle({ eyebrow, title, children }: { eyebrow: string; title: string; children?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="font-mono text-xs font-bold tracking-widest text-muted">{eyebrow}</p>
        <h1 className="mt-1 font-display text-3xl font-semibold tracking-tight">{title}</h1>
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

const SEV: Record<Severity, string> = {
  critical: "bg-coral text-coral-ink",
  high: "bg-peach text-peach-ink",
  medium: "bg-butter text-butter-ink",
  low: "bg-sky text-sky-ink",
  info: "bg-lav text-lav-ink",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`inline-flex rounded-md px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wide ${SEV[severity]}`}>
      {severity}
    </span>
  );
}

const INV: Record<InventoryStatus, string> = {
  active: "bg-mint text-mint-ink",
  zombie: "bg-coral text-coral-ink",
  shadow: "bg-lav text-lav-ink",
  retired: "bg-sky text-sky-ink",
  unknown: "bg-line text-muted",
};

export function InventoryBadge({ status }: { status: InventoryStatus }) {
  return (
    <span className={`inline-flex rounded-md px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wide ${INV[status]}`}>
      {status}
    </span>
  );
}

export function Pill({ children, tone = "lav" }: { children: React.ReactNode; tone?: "lav" | "sky" | "mint" | "peach" | "butter" | "coral" }) {
  const map = {
    lav: "bg-lav text-lav-ink",
    sky: "bg-sky text-sky-ink",
    mint: "bg-mint text-mint-ink",
    peach: "bg-peach text-peach-ink",
    butter: "bg-butter text-butter-ink",
    coral: "bg-coral text-coral-ink",
  };
  return <span className={`inline-flex rounded-md px-2 py-0.5 font-mono text-[11px] font-semibold ${map[tone]}`}>{children}</span>;
}

export function Method({ m }: { m: string }) {
  const tone = m === "GET" ? "text-sky-ink" : m === "POST" ? "text-peach-ink" : "text-lav-ink";
  return <span className={`font-mono text-xs font-bold ${tone}`}>{m}</span>;
}

export function Button({ children, onClick, tone = "primary", disabled, title }: {
  children: React.ReactNode;
  onClick?: () => void;
  tone?: "primary" | "quiet" | "danger" | "ok";
  disabled?: boolean;
  title?: string;
}) {
  const map = {
    primary: "bg-ink text-paper hover:opacity-90",
    quiet: "border border-line-strong bg-card text-ink hover:bg-lav",
    danger: "bg-coral text-coral-ink hover:brightness-95 border border-danger/30",
    ok: "bg-mint text-mint-ink hover:brightness-95",
  };
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${map[tone]}`}
    >
      {children}
    </button>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-xl border border-dashed border-line-strong px-4 py-8 text-center text-sm text-muted">{children}</p>;
}

export function Stat({ label, value, sub, tone = "card" }: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "card" | "lav" | "sky" | "mint" | "peach" | "coral";
}) {
  const bg = { card: "bg-card border border-line", lav: "bg-lav", sky: "bg-sky", mint: "bg-mint", peach: "bg-peach", coral: "bg-coral" }[tone];
  return (
    <div className={`rounded-2xl p-4 ${bg}`}>
      <p className="font-mono text-[11px] font-bold uppercase tracking-widest text-muted">{label}</p>
      <p className="mt-2 font-display text-3xl font-semibold tabular-nums leading-none">{value}</p>
      {sub && <p className="mt-2 text-xs text-muted">{sub}</p>}
    </div>
  );
}
