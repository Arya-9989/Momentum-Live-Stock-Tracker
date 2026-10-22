/**
 * Momentum's chart: a Streamlit v2 component on TradingView Lightweight Charts.
 *
 * Python sends a payload (types.ts) on every rerun. The chart is built once
 * and updated in place while its structure (panes, series, theme) is unchanged.
 * It sends one event back: `alert`, with the price level the viewer clicked,
 * when they choose "Set an alert at ..." in the readout.
 *
 * Motion (the merged motion pass, carried over from the Plotly chart):
 * - On mount, and whenever the symbol, range, interval or style changes (the
 *   payload's `key`), the series sweep in along the time axis, left to right.
 *   A curtain the colour of the surface covers the plot area (never the axes)
 *   and scales away with a transform, so only the compositor works.
 * - Under prefers-reduced-motion the curtain is never shown: the final chart at once.
 *
 * All text is set with textContent; nothing is built from markup strings.
 */

import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type MouseEventParams,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import { BandPrimitive } from "./band";
import { formatTime, formatValue } from "./format";
import type { Format, Payload, SeriesSpec } from "./types";

const FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
const REDUCED = "(prefers-reduced-motion: reduce)";

interface Component {
  data: Payload;
  parentElement: HTMLElement | ShadowRoot;
  setTriggerValue: (event: string, value: unknown) => void;
}

interface Entry {
  spec: SeriesSpec | null; // null for the candles
  label: string;
  format: Format;
  color: string;
  series: ISeriesApi<SeriesType, Time>;
}

interface State {
  root: HTMLElement;
  plot: HTMLElement;
  curtain: HTMLElement;
  readout: HTMLElement;
  pick: HTMLButtonElement;
  labels: HTMLElement[];
  chart: IChartApi;
  entries: Entry[];
  price: ISeriesApi<SeriesType, Time>;
  reference: IPriceLine | null;
  picked: IPriceLine | null;
  pickedPrice: number | null;
  structure: string;
  key: string;
  payload: Payload;
  observer: ResizeObserver;
  send: Component["setTriggerValue"];
}

const states = new WeakMap<object, State>();

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  return node;
}

/** What forces a rebuild: everything except the data itself. */
function structureOf(p: Payload): string {
  return JSON.stringify({
    panes: p.panes,
    series: p.series.map((s) => [s.id, s.kind, s.pane, s.color, s.width, s.dashed, s.areaTop, s.title]),
    candles: p.candles ? p.candles.label : null,
    bands: p.bands.map((b) => [b.pane, b.upper, b.lower, b.from, b.to, b.color]),
    theme: p.theme,
    height: p.height,
    intraday: p.intraday,
    zero: p.zeroLine ?? false,
  });
}

/** Axis labels: like the readout, but compact numbers keep one decimal at most
 * ("100M", "1.5B"), as the old chart's volume ticks did. */
function customFormat(format: Format) {
  return {
    type: "custom" as const,
    formatter: (v: number) =>
      format.kind === "compact"
        ? formatValue(v, { ...format, decimals: 1 }).replace(".0", "")
        : formatValue(v, format),
    minMove: format.kind === "compact" ? 1 : 10 ** -Math.max(format.decimals, 0),
  };
}

function toTime(t: number): UTCTimestamp {
  return t as UTCTimestamp;
}

function lineData(spec: SeriesSpec) {
  return spec.points.map(([t, v]) => (v === null ? { time: toTime(t) } : { time: toTime(t), value: v }));
}

// --------------------------------------------------------------------- build

function build(host: HTMLElement | ShadowRoot, payload: Payload, send: State["send"]): State {
  const theme = payload.theme;
  const root = el("figure", "mm-chart");
  root.setAttribute("aria-label", payload.alt);
  root.style.height = `${payload.height}px`;
  const plot = el("div", "mm-chart-plot");
  plot.setAttribute("aria-hidden", "true"); // the figure's label and the price table carry it
  const curtain = el("div", "mm-chart-curtain");
  curtain.style.background = theme.surface;
  const readout = el("div", "mm-chart-readout");
  readout.setAttribute("aria-hidden", "true");
  const pick = el("button", "mm-chart-pick");
  pick.type = "button";
  pick.hidden = true;
  root.append(plot, curtain, readout, pick);
  host.appendChild(root);

  const chart = createChart(plot, {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: theme.secondary,
      fontFamily: FONT,
      fontSize: 12,
      attributionLogo: true, // the link to tradingview.com the licence asks for
      panes: { separatorColor: theme.grid, separatorHoverColor: theme.axis, enableResize: false },
    },
    grid: { vertLines: { visible: false }, horzLines: { color: theme.grid } },
    rightPriceScale: { borderVisible: false, alignLabels: true },
    timeScale: {
      borderVisible: false,
      timeVisible: payload.intraday,
      secondsVisible: false,
      fixLeftEdge: true,
      fixRightEdge: true,
      lockVisibleTimeRangeOnResize: true,
    },
    crosshair: {
      mode: CrosshairMode.Magnet,
      vertLine: { color: theme.axis, width: 1, style: LineStyle.Solid, labelBackgroundColor: theme.ink },
      horzLine: { color: theme.axis, width: 1, style: LineStyle.Dotted, labelBackgroundColor: theme.ink },
    },
    handleScale: { axisPressedMouseMove: false },
  });

  const entries: Entry[] = [];
  let price: ISeriesApi<SeriesType, Time> | null = null;
  if (payload.candles) {
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: theme.up,
      downColor: theme.down,
      borderUpColor: theme.up,
      borderDownColor: theme.down,
      wickUpColor: theme.up,
      wickDownColor: theme.down,
      priceFormat: customFormat(payload.candles.format),
      priceLineVisible: false,
    });
    price = candles;
    entries.push({ spec: null, label: payload.candles.label, format: payload.candles.format, color: theme.ink, series: candles });
  }
  for (const spec of payload.series) {
    const common = {
      priceFormat: customFormat(spec.format),
      lastValueVisible: spec.lastValue ?? false,
      priceLineVisible: false,
      title: spec.title ?? "",
      crosshairMarkerVisible: spec.kind !== "histogram",
    };
    let series: ISeriesApi<SeriesType, Time>;
    if (spec.kind === "area") {
      series = chart.addSeries(
        AreaSeries,
        { ...common, lineColor: spec.color, lineWidth: 2, topColor: spec.areaTop ?? spec.color, bottomColor: "rgba(0,0,0,0)" },
        spec.pane,
      );
    } else if (spec.kind === "histogram") {
      series = chart.addSeries(HistogramSeries, { ...common, color: spec.color }, spec.pane);
    } else {
      series = chart.addSeries(
        LineSeries,
        {
          ...common,
          color: spec.color,
          lineWidth: (spec.width ?? 1.5) as 1,
          lineStyle: spec.dashed ? LineStyle.Dotted : LineStyle.Solid,
        },
        spec.pane,
      );
    }
    if (price === null && spec.pane === 0) price = series;
    entries.push({ spec, label: spec.label, format: spec.format, color: spec.color, series });
  }
  if (price === null) price = entries[0].series;
  // Room above the price line for the readout (resized to its height in layout()).
  price.priceScale().applyOptions({ scaleMargins: { top: 0.16, bottom: 0.04 } });

  // Panes: relative heights, fixed ranges, and a label in each corner.
  const panes = chart.panes();
  payload.panes.forEach((pane, i) => {
    panes[i]?.setStretchFactor(pane.weight);
  });
  for (const entry of entries) {
    const range = entry.spec ? payload.panes[entry.spec.pane]?.fixedRange : undefined;
    if (range) {
      entry.series.applyOptions({
        autoscaleInfoProvider: () => ({ priceRange: { minValue: range[0], maxValue: range[1] } }),
      });
    }
  }
  const labels = payload.panes.map((pane) => {
    const label = el("div", "mm-chart-pane-label");
    label.textContent = pane.label;
    label.style.color = theme.secondary;
    label.hidden = !pane.label;
    root.appendChild(label);
    return label;
  });

  const state: State = {
    root, plot, curtain, readout, pick, labels, chart, entries, price,
    reference: null, picked: null, pickedPrice: null,
    structure: structureOf(payload), key: "", payload,
    observer: new ResizeObserver(() => layout(state)), send,
  };

  // Bands draw behind the series they belong to.
  const times = (payload.candles ? payload.candles.bars.map((b) => b[0]) : payload.series[0]?.points.map((p) => p[0])) ?? [];
  for (const band of payload.bands) {
    const host = entries.find((e) => e.spec && e.spec.pane === band.pane)?.series ?? price;
    const edge = (id: string | undefined, level: number | undefined) => {
      if (level !== undefined) return level;
      const spec = payload.series.find((s) => s.id === id);
      const values = new Map(spec ? spec.points : []);
      return (t: number) => values.get(t) ?? null;
    };
    host.attachPrimitive(new BandPrimitive(band.color, times, edge(band.upper, band.from), edge(band.lower, band.to)));
  }

  chart.subscribeCrosshairMove((param) => showReadout(state, param));
  chart.subscribeClick((param) => onClick(state, param));
  chart.subscribeDblClick(() => chart.timeScale().fitContent());
  pick.addEventListener("click", () => {
    if (state.pickedPrice !== null) state.send("alert", { price: state.pickedPrice });
  });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape") clearPick(state);
  });
  state.observer.observe(root);
  return state;
}

// --------------------------------------------------------------------- update

function update(state: State, payload: Payload): void {
  state.payload = payload;
  const theme = payload.theme;
  for (const entry of state.entries) {
    if (entry.spec === null && payload.candles) {
      entry.series.setData(
        payload.candles.bars.map(([t, o, h, l, c]) => ({ time: toTime(t), open: o, high: h, low: l, close: c })),
      );
    } else if (entry.spec) {
      const spec = payload.series.find((s) => s.id === entry.spec!.id) ?? entry.spec;
      entry.spec = spec;
      entry.series.setData(lineData(spec));
    }
  }
  if (state.reference) state.price.removePriceLine(state.reference);
  state.reference = null;
  if (payload.reference) {
    state.reference = state.price.createPriceLine({
      price: payload.reference.price,
      color: theme.tertiary,
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
      axisLabelVisible: true,
      title: payload.reference.label,
    });
  }
  if (payload.zeroLine) {
    state.reference = state.price.createPriceLine({
      price: 0, color: theme.axis, lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: false, title: "",
    });
  }
  clearPick(state);
  state.chart.timeScale().fitContent();
  showReadout(state, null);
  if (payload.key !== state.key) {
    state.key = payload.key;
    sweep(state);
  }
  requestAnimationFrame(() => layout(state));
}

/** Pane labels sit in each pane's corner; the curtain covers the plot area only. */
function layout(state: State): void {
  const panes = state.chart.panes();
  let top = 0;
  panes.forEach((pane, i) => {
    const label = state.labels[i];
    if (label) label.style.transform = `translate(0, ${top + 4}px)`;
    top += pane.getHeight() + 1;
  });
  state.curtain.style.width = `${state.chart.timeScale().width()}px`;
  state.curtain.style.height = `${Math.max(top - 1, 0)}px`;
  // Keep the price line clear of the readout, however many lines it wraps to.
  const paneHeight = panes[0]?.getHeight() ?? 0;
  if (paneHeight > 0) {
    const share = Math.min(0.45, (state.readout.offsetHeight + 12) / paneHeight);
    const margins = state.price.priceScale().options().scaleMargins;
    if (Math.abs(margins.top - share) > 0.01) {
      state.price.priceScale().applyOptions({ scaleMargins: { top: share, bottom: 0.04 } });
    }
  }
}

function sweep(state: State): void {
  const curtain = state.curtain;
  curtain.classList.remove("mm-sweeping");
  if (matchMedia(REDUCED).matches) return; // final state at once
  void curtain.offsetWidth; // restart the animation for a new key
  curtain.classList.add("mm-sweeping");
}

// --------------------------------------------------------------------- readout

function row(color: string | null, value: string, name: string): HTMLElement {
  const line = el("div", "mm-chart-row");
  if (color) {
    const swatch = el("span", "mm-chart-swatch");
    swatch.style.background = color;
    line.appendChild(swatch);
  }
  const strong = el("b", "mm-chart-value");
  strong.textContent = value;
  const label = el("span", "mm-chart-name");
  label.textContent = name;
  line.append(strong, label);
  return line;
}

function valueAt(entry: Entry, param: MouseEventParams<Time> | null, payload: Payload): number | null {
  const point = param?.seriesData.get(entry.series) as { value?: number; close?: number } | undefined;
  if (point) return point.close ?? point.value ?? null;
  if (param) return null;
  // No hover: the latest value.
  if (entry.spec === null) {
    const bars = payload.candles?.bars ?? [];
    return bars.length ? bars[bars.length - 1][4] : null;
  }
  const points = entry.spec.points;
  for (let i = points.length - 1; i >= 0; i -= 1) if (points[i][1] !== null) return points[i][1];
  return null;
}

function showReadout(state: State, param: MouseEventParams<Time> | null): void {
  const payload = state.payload;
  const hovering = param !== null && param.time !== undefined;
  const time = hovering ? Number(param.time) : lastTime(payload);
  const rows: HTMLElement[] = [];
  const when = el("div", "mm-chart-time");
  when.textContent = time === null ? "" : formatTime(time, payload.intraday);
  rows.push(when);
  for (const entry of state.entries) {
    const value = valueAt(entry, hovering ? param : null, payload);
    if (value === null && entry.spec?.kind === "histogram") continue;
    const swatch = entry.spec === null || entry.spec.kind !== "line" ? null : entry.color;
    rows.push(row(swatch, formatValue(value, entry.format), entry.label));
  }
  state.readout.replaceChildren(...rows);
}

function lastTime(payload: Payload): number | null {
  if (payload.candles?.bars.length) return payload.candles.bars[payload.candles.bars.length - 1][0];
  const points = payload.series[0]?.points;
  return points?.length ? points[points.length - 1][0] : null;
}

// --------------------------------------------------------------------- pick

function onClick(state: State, param: MouseEventParams<Time>): void {
  const pick = state.payload.pick;
  if (!pick || !param.point || (param.paneIndex ?? 0) !== 0) return;
  const price = state.price.coordinateToPrice(param.point.y);
  if (price === null || !Number.isFinite(price) || price <= 0) return;
  const format = state.entries[0].format;
  const decimals = format.decimals;
  const rounded = Number(price.toFixed(decimals));
  clearPick(state);
  state.pickedPrice = rounded;
  state.picked = state.price.createPriceLine({
    price: rounded,
    color: state.payload.theme.ink,
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    axisLabelVisible: true,
    title: "",
  });
  state.pick.textContent = `${pick.label} ${formatValue(rounded, format)}`;
  state.pick.hidden = false;
}

function clearPick(state: State): void {
  if (state.picked) state.price.removePriceLine(state.picked);
  state.picked = null;
  state.pickedPrice = null;
  state.pick.hidden = true;
}

// --------------------------------------------------------------------- entry

export default function (component: Component): () => void {
  const { data, parentElement, setTriggerValue } = component;
  let state = states.get(parentElement);
  if (state && state.structure !== structureOf(data)) {
    destroy(state);
    states.delete(parentElement);
    state = undefined;
  }
  if (!state) {
    state = build(parentElement, data, setTriggerValue);
    states.set(parentElement, state);
  }
  state.send = setTriggerValue;
  state.root.setAttribute("aria-label", data.alt);
  update(state, data);
  const current = state;
  return () => {
    if (states.get(parentElement) === current && !current.root.isConnected) {
      destroy(current);
      states.delete(parentElement);
    }
  };
}

function destroy(state: State): void {
  state.observer.disconnect();
  state.chart.remove();
  state.root.remove();
}
