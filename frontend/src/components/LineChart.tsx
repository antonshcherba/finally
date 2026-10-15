'use client';
import { useEffect, useRef } from 'react';
import { ColorType, LineSeries, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';

interface Props {
  data: { time: number; value: number }[];
  color?: string;
  mini?: boolean;
}

export default function LineChart({ data, color = '#209dd7', mini = false }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Line'> | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#8b949e', attributionLogo: false },
      grid: mini
        ? { vertLines: { visible: false }, horzLines: { visible: false } }
        : { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
      rightPriceScale: { visible: !mini, borderColor: '#30363d' },
      timeScale: { visible: !mini, borderColor: '#30363d', timeVisible: true, secondsVisible: true },
      handleScroll: !mini,
      handleScale: !mini,
      crosshair: mini ? { vertLine: { visible: false }, horzLine: { visible: false } } : undefined,
      leftPriceScale: { visible: false },
    });
    chart.current = c;
    series.current = c.addSeries(LineSeries, {
      color,
      lineWidth: mini ? 1 : 2,
      priceLineVisible: !mini,
      lastValueVisible: !mini,
    });
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
    };
  }, [mini, color]);

  useEffect(() => {
    series.current?.setData(data.map((d) => ({ time: d.time as UTCTimestamp, value: d.value })));
    if (!mini) chart.current?.timeScale().fitContent();
  }, [data, mini, color]);

  return <div ref={el} className="h-full w-full" />;
}
