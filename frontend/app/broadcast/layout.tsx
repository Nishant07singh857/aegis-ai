import type { ReactNode } from 'react';

/**
 * AEGIS AI — layout for the /broadcast/* routes.
 *
 * Imports the Phase 8 graphics-template animation stylesheet (global CSS
 * can only be imported from a layout in the App Router), mirroring
 * app/overlays/layout.tsx. /broadcast/output renders DirectorOutputView,
 * which now renders graphics templates.
 */

import '@/components/graphics/graphics.css';

export default function BroadcastLayout({
  children,
}: {
  children: ReactNode;
}) {
  return <>{children}</>;
}
