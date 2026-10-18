'use client';

/**
 * AEGIS AI — graphics template integration (Phase 8, W3).
 *
 * Glue between the director state (lib/director.ts), the template
 * registry (components/graphics/registry.ts) and every surface that
 * renders broadcast graphics (overlay pages, DirectorOutputView, the
 * template gallery, the director console picker).
 *
 * Template resolution precedence (documented in docs/GRAPHICS.md):
 *   1. an explicit ?template=<id> query param on an overlay page
 *      (OBS operators pinning a look for one source),
 *   2. the director state's graphics block (operator's choice via the
 *      gallery / console picker, live over WS "director.state"),
 *   3. the per-type DEFAULT_TEMPLATES fallback.
 * Unknown ids at any level fall back to the type default — a bad URL or
 * a stale id can never render a blank overlay.
 */

import {
  DEFAULT_TEMPLATES,
  TEMPLATES,
  getTemplate,
  type GraphicType,
  type TemplateDef,
} from '@/components/graphics/registry';
import type { DirectorGraphics, DirectorState } from './director';

/** Canonical display order of the four graphic types. */
export const GRAPHIC_TYPES: readonly GraphicType[] = [
  'scorebug',
  'lower-third',
  'standings',
  'killfeed',
];

export const GRAPHIC_TYPE_LABELS: Record<GraphicType, string> = {
  scorebug: 'Scorebug',
  'lower-third': 'Lower third',
  standings: 'Standings',
  killfeed: 'Killfeed',
};

/** DirectorState.graphics key holding the active template id per type. */
export const GRAPHIC_STATE_KEYS: Record<GraphicType, keyof DirectorGraphics> = {
  scorebug: 'scorebug_template_id',
  'lower-third': 'lowerthird_template_id',
  standings: 'standings_template_id',
  killfeed: 'killfeed_template_id',
};

/** All registered templates of one type, in registry order. */
export function templatesFor(type: GraphicType): TemplateDef[] {
  return TEMPLATES.filter((t) => t.type === type);
}

/**
 * The template id the director state selects for a type, or the type
 * default when the state is absent / predates Phase 8.
 */
export function stateTemplateId(
  state: DirectorState | null,
  type: GraphicType
): string {
  const fromState = state?.graphics?.[GRAPHIC_STATE_KEYS[type]];
  return typeof fromState === 'string' && fromState
    ? fromState
    : DEFAULT_TEMPLATES[type];
}

/**
 * Resolve the template to render for a graphic type.
 *
 * @param type      graphic type
 * @param paramId   explicit ?template=<id> override (null/undefined = none)
 * @param state     live director state (null = not yet received)
 * @returns the TemplateDef to render — never null; unknown ids fall back
 *          to the per-type default inside getTemplate.
 */
export function resolveTemplate(
  type: GraphicType,
  paramId: string | null | undefined,
  state: DirectorState | null
): TemplateDef {
  const candidate =
    typeof paramId === 'string' && paramId.trim()
      ? paramId.trim()
      : stateTemplateId(state, type);
  return getTemplate(type, candidate);
}

/** True when the given id is the template the director state selects. */
export function isActiveTemplate(
  state: DirectorState | null,
  type: GraphicType,
  id: string
): boolean {
  return resolveTemplate(type, null, state).id === id;
}

export type { GraphicType, TemplateDef };
export { DEFAULT_TEMPLATES };
