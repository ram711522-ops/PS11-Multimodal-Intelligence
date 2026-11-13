import { Activity, CalendarDays } from 'lucide-react';
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { EmptyState } from '../common/EmptyState';
import { formatDate } from '../../utils/formatters';
import type { InvestigationSummary } from '../../types/investigation';

interface ActivityPoint { date: string; label: string; count: number }

function makeActivitySeries(investigations: InvestigationSummary[]): ActivityPoint[] {
  const validDates = investigations
    .map((item) => new Date(item.created_at))
    .filter((date) => !Number.isNaN(date.getTime()));
  if (validDates.length === 0) return [];

  const counts = new Map<string, number>();
  for (const value of validDates) {
    const key = value.toISOString().slice(0, 10);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const lastDay = new Date(Math.max(...validDates.map((date) => date.getTime())));
  lastDay.setUTCHours(0, 0, 0, 0);
  return Array.from({ length: 14 }, (_, index) => {
    const day = new Date(lastDay);
    day.setUTCDate(lastDay.getUTCDate() - (13 - index));
    const key = day.toISOString().slice(0, 10);
    return { date: key, label: formatDate(day), count: counts.get(key) ?? 0 };
  });
}

function ActivityTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: ActivityPoint }> }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <span>{point.label}</span>
      <strong>{point.count} {point.count === 1 ? 'investigation' : 'investigations'}</strong>
    </div>
  );
}

export function InvestigationChart({ investigations }: { investigations: InvestigationSummary[] }) {
  const series = makeActivitySeries(investigations);
  return (
    <section className="panel activity-panel">
      <div className="panel-heading">
        <div><div className="eyebrow"><Activity size={13} /> ACTIVITY</div><h2>Investigation activity</h2></div>
        {series.length > 0 && <span className="panel-meta"><CalendarDays size={14} /> 14-day window · UTC</span>}
      </div>
      {series.length === 0 ? (
        <EmptyState compact title="No activity to chart" description="New investigations will appear here once they’re created." />
      ) : (
        <div className="activity-chart" role="img" aria-label="Daily investigation creation activity over the 14 days ending with the latest returned investigation">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series} margin={{ top: 8, right: 10, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="#282830" strokeDasharray="3 5" vertical={false} />
              <XAxis dataKey="label" tick={{ fill: '#777783', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={22} />
              <YAxis allowDecimals={false} width={38} tick={{ fill: '#777783', fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip content={<ActivityTooltip />} cursor={{ stroke: '#6660a8', strokeDasharray: '4 4' }} />
              <Line type="monotone" dataKey="count" stroke="#9a8cff" strokeWidth={2.5} activeDot={{ r: 5, fill: '#c8beff', stroke: '#24212f', strokeWidth: 3 }} dot={{ r: 3, fill: '#a99aff', strokeWidth: 0 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      {investigations.length > 0 && <div className="chart-footnote">Activity is calculated from the latest records returned by the API.</div>}
    </section>
  );
}
