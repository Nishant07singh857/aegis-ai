import type { ReactNode } from 'react';

/**
 * AEGIS AI — layout for the /watch viewer route.
 *
 * Imports the Phase 8 graphics-template animation stylesheet (global CSS
 * can only be imported from a layout in the App Router). /watch renders
 * DirectorOutputView, which now renders graphics templates.
 */

import '@/components/graphics/graphics.css';
import '@/components/graphics/animated/animated.css';

export default function WatchLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
