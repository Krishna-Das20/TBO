"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useApi } from "@/lib/api";
import type { GatewayStatus } from "@/lib/types";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/findings", label: "Findings" },
  { href: "/inventory", label: "API inventory" },
  { href: "/runs", label: "Runs" },
  { href: "/safety", label: "Safety" },
];

export function Nav() {
  const path = usePathname();
  const { data: gw, error } = useApi<GatewayStatus>("gateway", 5000);
  const targets = gw ? Object.values(gw.targets) : [];
  const halted = targets.some((t) => t.halted);
  const state = error ? "offline" : !gw ? "…" : halted ? "halted" : "testing allowed";
  const tone = error || halted ? "bg-coral text-coral-ink" : "bg-mint text-mint-ink";

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-paper/90 backdrop-blur">
      <div className="mx-auto flex w-full max-w-[1320px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6 lg:px-8">
        <Link href="/" className="flex items-center gap-3">
          <Image src="/hawk-logo.png" alt="HAWK" width={104} height={32} className="dark-logo h-7 w-auto" priority />
          <span className="hidden font-mono text-xs font-bold tracking-widest text-muted sm:inline">CONSOLE</span>
        </Link>
        <nav className="order-3 -mx-1 flex w-full gap-1 overflow-x-auto [scrollbar-width:none] md:order-none md:w-auto">
          {LINKS.map((l) => {
            const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                  active ? "bg-lav text-lav-ink" : "text-muted hover:bg-card hover:text-ink"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden font-mono text-xs text-muted sm:inline">target: voyage-staging</span>
          <span className={`rounded-lg px-2.5 py-1 font-mono text-xs font-bold ${tone}`}>
            <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-current align-middle" />
            {state}
          </span>
        </div>
      </div>
    </header>
  );
}
