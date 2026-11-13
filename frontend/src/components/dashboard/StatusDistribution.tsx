import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { EmptyState } from '../common/EmptyState';
import { titleCaseStatus } from '../../utils/formatters';
import type { InvestigationSummary } from '../../types/investigation';

const statuses = [
  { key: 'received', color: '#9b8cff' },
  { key: 'awaiting_media', color: '#6d7cf5' },
  { key: 'queued', color: '#49b9b0' },
  { key: 'processing', color: '#e1aa67' },
  { key: 'completed', color: '#72bd91' },
  { key: 'partial', color: '#b38bd9' },
  { key: 'failed', color: '#dc777d' },
];

export function StatusDistribution({ investigations }: { investigations: InvestigationSummary[] }) {
  const counts = new Map<string, number>();
  investigations.forEach(({ status }) => counts.set(status, (counts.get(status) ?? 0) + 1));
  const chartData = statuses.map(({ key, color }) => ({ name: titleCaseStatus(key), value: counts.get(key) ?? 0, color }));
  const activeData = chartData.filter(({ value }) => value > 0);
  const total = investigations.length;

  return (
    <section className="panel status-panel">
      <div className="panel-heading">
        <div><div className="eyebrow">CURRENT WORKFLOW</div><h2>Status distribution</h2></div>
        {total > 0 && <span className="panel-meta">{total} loaded</span>}
      </div>
      {total === 0 ? (
        <EmptyState compact title="No statuses yet" description="Status breakdown appears when investigations are available." />
      ) : (
        <div className="status-content">
          <div className="status-donut-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={activeData} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius="68%" outerRadius="88%" paddingAngle={4} stroke="none" isAnimationActive={false}>
                  {activeData.map((entry) => <Cell key={entry.name} fill={entry.color} />)}
                </Pie>
                <Tooltip formatter={(value, name) => [`${value} investigations`, name]} contentStyle={{ background: '#1b1a22', border: '1px solid #34323e', borderRadius: 10, color: '#f3f1f8', fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
            <div className="donut-center"><strong>{total}</strong><span>loaded</span></div>
          </div>
          <div className="status-legend">
            {chartData.map(({ name, value, color }) => (
              <div className="status-legend-row" key={name}>
                <span className="status-legend-name"><i style={{ backgroundColor: color }} />{name}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
        </div>
      )}
      {total > 0 && <div className="chart-footnote">Counts reflect the records returned by the API.</div>}
    </section>
  );
}
