/**
 * AEGIS AI — data-source marker helpers (Integrity Fix Pack, C6).
 *
 * Contract with the backend: operator state that used to be fabricated
 * (devices / agents / system health / social / training / render blocks)
 * is NOT served at all when DEMO_MODE is off. When DEMO_MODE is on, every
 * demo block carries a `"source": "demo"` marker — at the block level,
 * on each row of a list block, or on the WS frame / REST envelope that
 * carries the block. These helpers detect the marker in any of those
 * positions so pages can badge demo data instead of presenting it as
 * real, and render honest empty states when the block is absent.
 */

/** True when `value` (or anything nested in it, up to a few levels) is
 *  explicitly marked as demo-sourced. */
export function hasDemoSource(value: unknown, depth = 0): boolean {
  if (value === null || value === undefined || depth > 4) return false;
  if (Array.isArray(value)) {
    return value.some((item) => hasDemoSource(item, depth + 1));
  }
  if (typeof value === "object") {
    const rec = value as Record<string, unknown>;
    if (rec.source === "demo") return true;
    return Object.values(rec).some((v) => hasDemoSource(v, depth + 1));
  }
  return false;
}

/** The `"demo"` marker for a block, or null when the block is not
 *  demo-marked (real data, or no data yet). */
export function demoSourceOf(value: unknown): "demo" | null {
  return hasDemoSource(value) ? "demo" : null;
}
