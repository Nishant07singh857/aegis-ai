/**
 * AEGIS AI — layout for the /overlays/* broadcast routes.
 *
 * Imports the Phase 8 graphics-template animation stylesheet (global CSS
 * can only be imported from a layout in the App Router). Nothing else:
 * overlay pages stay transparent and self-contained for OBS capture.
 */

import '@/components/graphics/graphics.css';
import type { ReactNode } from 'react';

export default function OverlaysLayout({
  children,
}: {
  children: ReactNode;
}) {
  return <>{children}</>;
}
