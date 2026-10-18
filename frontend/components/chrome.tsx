'use client';

/**
 * AEGIS AI — shell chrome switch.
 *
 * Everywhere in the operator console we render the TopBar + padded <main>.
 * Routes under /overlays/*, /broadcast/* and /watch are broadcast sources
 * and must be broadcast-clean: no nav, no topbar, no padded wrapper — just
 * the page content itself on a transparent page (the overlay/director pages
 * inject `background: transparent` via <OverlayRoot>). /watch draws its own
 * minimal overlay header (LIVE badge + logout) instead of the TopBar.
 */

import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { TopBar } from '@/components/topbar';

export function Chrome({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  if (
    pathname?.startsWith('/overlays') ||
    pathname?.startsWith('/broadcast') ||
    pathname?.startsWith('/watch')
  ) {
    return <>{children}</>;
  }

  return (
    <>
      <TopBar />
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{children}</main>
    </>
  );
}
