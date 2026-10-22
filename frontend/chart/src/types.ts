/**
 * The JSON the Python side sends (momentum/ui/chart.py builds it). Times are
 * exchange-local wall-clock times encoded as UTC seconds, so the axis shows the
 * exchange's own hours for every viewer.
 */

export type Point = [number, number | null];
export type Candle = [number, number, number, number, number]; // time, open, high, low, close

export interface Format {
  kind: "price" | "compact" | "percent" | "plain";
  prefix?: string;
  suffix?: string;
  decimals: number;
  signed?: boolean;
}

export interface Theme {
  surface: string;
  ink: string;
  secondary: string;
  tertiary: string;
  grid: string;
  axis: string;
  up: string;
  down: string;
  band: string;
  neutralBar: string;
  hoverBg: string;
  hoverBorder: string;
}

export interface Pane {
  label: string; // shown in the pane's top-left corner ("" for the price pane)
  weight: number; // relative height
  fixedRange?: [number, number]; // RSI is always 0-100
}

export interface SeriesSpec {
  id: string;
  label: string;
  kind: "line" | "area" | "histogram";
  pane: number;
  color: string;
  width?: number;
  dashed?: boolean;
  points: Point[];
  format: Format;
  areaTop?: string; // area series: gradient colour at the top
  lastValue?: boolean; // show the last value (and the title) on the price scale
  title?: string;
}

export interface Band {
  pane: number;
  color: string;
  upper?: string; // series ids, for a band between two lines (Bollinger)
  lower?: string;
  from?: number; // constant levels, for a zone (RSI 30-70)
  to?: number;
}

export interface Payload {
  key: string; // symbol | range | interval | style: a change replays the sweep
  alt: string;
  height: number;
  intraday: boolean;
  theme: Theme;
  panes: Pane[];
  candles?: { bars: Candle[]; format: Format; label: string };
  series: SeriesSpec[];
  bands: Band[];
  reference?: { price: number; label: string };
  zeroLine?: boolean; // the comparison chart's 0% baseline
  pick?: { label: string }; // "Set an alert at ..." from a click on the price pane
}
