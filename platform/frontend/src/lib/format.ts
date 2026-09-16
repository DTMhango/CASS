/**
 * Formatting for analytical display.
 *
 * Monetary values arrive from the API as strings because they are Decimal on
 * the server. They stay strings here until the moment of display: converting a
 * TIV to a JavaScript number to format it would silently lose precision on a
 * large portfolio, which is exactly the error section 8 spends a page
 * preventing on the backend.
 */

/**
 * The compact tiers, largest first.
 *
 * The suffix is ours rather than Intl's compact notation, which reads its
 * abbreviations from whatever CLDR the runtime happens to ship: recent data
 * renders en-GB as "9.9m" and "1.3bn" where older data renders "9.9M" and
 * "1.3B". A loss figure should not change its wording because a machine
 * updated its locale data, so the scaling is Intl's and the wording is not.
 */
const COMPACT_TIERS: readonly { scale: number; suffix: string }[] = [
  { scale: 1e12, suffix: "T" },
  { scale: 1e9, suffix: "B" },
  { scale: 1e6, suffix: "M" },
];

const FULL = new Intl.NumberFormat("en-GB", {
  maximumFractionDigits: 0,
});

const COUNT = new Intl.NumberFormat("en-GB");

/**
 * Format a decimal string for a dense table or tile.
 *
 * Large values are shown compactly because a loss table is read for magnitude,
 * not for pence. The exact value stays available as a title attribute wherever
 * this is used in a tile.
 */
export function formatMoney(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  if (Math.abs(numeric) >= 1_000_000) return formatCompact(numeric);
  return FULL.format(numeric);
}

/**
 * One decimal place against the largest tier the value reaches.
 *
 * The tier is chosen after rounding rather than before it, so a figure that
 * rounds up out of its tier is named by the one it lands in: 999,999,999 is
 * 1B, not 1000M.
 */
function formatCompact(numeric: number): string {
  for (const tier of COMPACT_TIERS) {
    const scaled = Number((numeric / tier.scale).toFixed(1));
    if (Math.abs(scaled) < 1) continue;
    return `${scaled.toFixed(1).replace(/\.0$/, "")}${tier.suffix}`;
  }
  return FULL.format(numeric);
}

/** The exact figure, for tooltips and exports. */
export function formatMoneyExact(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 2 }).format(numeric);
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return COUNT.format(value);
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" }).format(date);
}

/** Elapsed time in words, for the run monitor. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}
