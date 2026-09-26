import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "BetPredictor",
  description: "Personal football value-betting model — local only, paper mode by default.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <header className="border-b">
          <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 py-3">
            <Link href="/" className="font-semibold tracking-tight">
              BetPredictor
            </Link>
            <nav className="text-sm text-muted-foreground">
              <Link href="/" className="hover:text-foreground">
                Today
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>
        <footer className="border-t">
          <p className="mx-auto max-w-6xl px-4 py-3 text-xs text-muted-foreground">
            No model can guarantee wins. Bookmaker odds are efficient; this tool looks for small
            positive expected value and measures honestly whether it exists.
          </p>
        </footer>
      </body>
    </html>
  );
}
