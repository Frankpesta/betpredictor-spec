"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Today" },
  { href: "/fixtures", label: "Fixtures" },
  { href: "/history", label: "History" },
  { href: "/performance", label: "Performance" },
  { href: "/model", label: "Model" },
  { href: "/data-health", label: "Data health" },
] as const;

export function NavLinks() {
  const path = usePathname();
  return (
    <nav className="flex flex-wrap gap-0.5 rounded-xl bg-muted/70 p-1 text-sm ring-1 ring-border/60">
      {LINKS.map(({ href, label }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "rounded-lg px-2.5 py-1.5 font-medium text-muted-foreground transition-colors hover:text-foreground",
              active && "bg-card text-foreground shadow-sm ring-1 ring-border/70",
            )}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
