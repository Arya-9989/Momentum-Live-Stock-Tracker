/**
 * A filled band behind a series: between two lines (Bollinger Bands) or between
 * two constant levels (the RSI 30-70 zone). Lightweight Charts has no
 * "fill between", so this is a series primitive that draws on the pane's canvas
 * in its background layer, under the lines.
 */

import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";

type Level = number | ((time: number) => number | null);

interface Segment {
  x: number;
  top: number;
  bottom: number;
}

class BandRenderer implements IPrimitivePaneRenderer {
  constructor(
    private readonly segments: Segment[][],
    private readonly color: string,
  ) {}

  draw(): void {}

  drawBackground(target: CanvasRenderingTarget2D): void {
    target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      context.fillStyle = this.color;
      for (const run of this.segments) {
        if (run.length < 2) continue;
        context.beginPath();
        context.moveTo(run[0].x * hr, run[0].top * vr);
        for (const p of run) context.lineTo(p.x * hr, p.top * vr);
        for (let i = run.length - 1; i >= 0; i -= 1) context.lineTo(run[i].x * hr, run[i].bottom * vr);
        context.closePath();
        context.fill();
      }
    });
  }
}

class BandView implements IPrimitivePaneView {
  constructor(private readonly band: BandPrimitive) {}

  zOrder(): "bottom" {
    return "bottom";
  }

  renderer(): IPrimitivePaneRenderer | null {
    return new BandRenderer(this.band.segments(), this.band.color);
  }
}

export class BandPrimitive implements ISeriesPrimitive<Time> {
  private params: SeriesAttachedParameter<Time, SeriesType> | null = null;
  private readonly view = new BandView(this);

  constructor(
    readonly color: string,
    private readonly times: number[],
    private readonly upper: Level,
    private readonly lower: Level,
  ) {}

  attached(params: SeriesAttachedParameter<Time, SeriesType>): void {
    this.params = params;
  }

  detached(): void {
    this.params = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  /** Runs of contiguous points with both edges defined, in pixel coordinates. */
  segments(): Segment[][] {
    const params = this.params;
    if (params === null) return [];
    const series = params.series as ISeriesApi<SeriesType, Time>;
    const scale = params.chart.timeScale();
    const level = (edge: Level, time: number): number | null =>
      typeof edge === "number" ? edge : edge(time);
    const runs: Segment[][] = [];
    let run: Segment[] = [];
    for (const time of this.times) {
      const hi = level(this.upper, time);
      const lo = level(this.lower, time);
      const x = scale.timeToCoordinate(time as UTCTimestamp);
      const top = hi === null ? null : series.priceToCoordinate(hi);
      const bottom = lo === null ? null : series.priceToCoordinate(lo);
      if (x === null || top === null || bottom === null) {
        if (run.length) runs.push(run);
        run = [];
        continue;
      }
      run.push({ x, top, bottom });
    }
    if (run.length) runs.push(run);
    return runs;
  }
}
