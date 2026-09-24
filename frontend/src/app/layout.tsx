/*
 * WHY THIS EXISTS
 * The frame around every dashboard screen: the top bar with links to the
 * sessions list, the live meeting and settings, plus a line that names
 * anything in the backend that is still a placeholder.
 *
 * FAILURE IT PREVENTS
 * Someone mistaking canned output for real output (CLAUDE.md rule 6), and
 * getting lost between screens. It uses the computer's own fonts rather than
 * downloading web fonts, so building the dashboard never needs the internet.
 */
import type { Metadata } from "next";
import Link from "next/link";
import HealthBanner from "@/components/HealthBanner";
import "./globals.css";

export const metadata: Metadata = {
  title: "Meet AGI dashboard",
  description: "Live meeting view, session review and settings for Meet AGI.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link href="/" className="brand">Meet AGI</Link>
          <nav>
            <Link href="/">Sessions</Link>
            <Link href="/live">Live meeting</Link>
            <Link href="/settings">Settings</Link>
          </nav>
        </header>
        <HealthBanner />
        <main>{children}</main>
      </body>
    </html>
  );
}
