import type { Format } from "./types";

const SUFFIXES: [number, string][] = [
  [1e12, "T"],
  [1e9, "B"],
  [1e6, "M"],
  [1e3, "K"],
];

function grouped(value: number, decimals: number): string {
  return value.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/** The same strings momentum.formatting produces: "$1,234.56", "120.50p", "1.2B", "+3.40%". */
export function formatValue(value: number | null | undefined, format: Format): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const decimals = format.decimals;
  // A value that rounds to zero prints as zero, never "-0.00".
  const v = Math.abs(value) < 0.5 * 10 ** -decimals ? 0 : value;
  const sign = v < 0 ? "-" : format.signed && v > 0 ? "+" : "";
  const abs = Math.abs(v);
  switch (format.kind) {
    case "compact": {
      for (const [threshold, suffix] of SUFFIXES) {
        if (abs >= threshold) return `${sign}${grouped(abs / threshold, decimals)}${suffix}`;
      }
      return `${sign}${grouped(abs, 0)}`;
    }
    case "percent":
      return `${sign}${grouped(abs, decimals)}%`;
    case "price":
      return `${sign}${format.prefix ?? ""}${grouped(abs, decimals)}${format.suffix ?? ""}`;
    default:
      return `${sign}${grouped(abs, decimals)}`;
  }
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Wed Oct 22, 2026" or, for intraday bars, "Wed Oct 22, 09:35" (exchange-local). */
export function formatTime(seconds: number, intraday: boolean): string {
  const d = new Date(seconds * 1000);
  const day = `${DAYS[d.getUTCDay()]} ${MONTHS[d.getUTCMonth()]} ${String(d.getUTCDate()).padStart(2, "0")}`;
  if (!intraday) return `${day}, ${d.getUTCFullYear()}`;
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${day}, ${hh}:${mm}`;
}
