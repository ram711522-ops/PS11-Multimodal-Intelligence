import { useOutletContext, useNavigate } from 'react-router-dom';
import { AlertTriangle, ArrowUpRight, Database, Image, Plus, RefreshCw } from 'lucide-react';
import { ErrorState } from '../components/common/ErrorState';
import { LoadingState } from '../components/common/LoadingState';
import { MetricCard } from '../components/dashboard/MetricCard';
import { InvestigationChart } from '../components/dashboard/InvestigationChart';
import { RecentInvestigations } from '../components/dashboard/RecentInvestigations';
import { StatusDistribution } from '../components/dashboard/StatusDistribution';
import type { InvestigationsState } from '../hooks/useInvestigations';

export function Dashboard() {
  const { data, isLoading, isRefreshing, error, refresh } = useOutletContext<InvestigationsState>();
  const navigate = useNavigate();

  if (isLoading && !data) {
    return <div className="dashboard-loading"><LoadingState rows={5} /><div className="loading-grid"><LoadingState rows={4} /><LoadingState rows={4} /></div><RecentInvestigations investigations={[]} isLoading /></div>;
  }
  if (error && !data) return <ErrorState message={error} onRetry={() => void refresh()} />;

  const history = data?.history;
  const investigations = history?.items ?? [];
  const total = history?.total ?? 0;
  const loadedCount = investigations.length;
  const limited = total > loadedCount;
  const countStatus = (status: string) => investigations.filter((item) => item.status === status).length;
  const scopeNote = limited ? `Among latest ${loadedCount} loaded` : 'Across loaded investigations';

  return (
    <div className="dashboard-page flex flex-col">
      {error && data && (
        <div className="inline-warning" role="status"><AlertTriangle size={16} /><span>Refresh failed. Showing the last successfully loaded data.</span><button onClick={() => void refresh()}>Retry</button></div>
      )}

      <section className="welcome-row flex items-center justify-between">
        <div className="welcome-copy">
          <div className="eyebrow welcome-eyebrow"><span className="eyebrow-line" /> LIVE WORKSPACE</div>
          <h2>Intelligence Overview</h2>
          <p>Monitor investigations, assess information credibility and explore emerging patterns.</p>
          <div className="welcome-context"><span className={`live-indicator ${data?.health?.status === 'ok' ? '' : 'live-indicator--muted'}`} />{data?.health?.status === 'ok' ? 'Connected to Phase 2 API' : 'Investigation data loaded · API health unavailable'}</div>
        </div>
        <div className="welcome-actions">
          <button className="button button--secondary refresh-button" onClick={() => void refresh()} disabled={isRefreshing} aria-label="Refresh dashboard data">
            <RefreshCw size={15} className={isRefreshing ? 'spin' : ''} />{isRefreshing ? 'Refreshing' : 'Refresh'}
          </button>
          <button className="button button--primary" onClick={() => navigate('/new-investigation')}><Plus size={17} /> New investigation</button>
        </div>
        <div className="welcome-orbit" aria-hidden="true"><span /><i /><b /></div>
      </section>

      <section className="metric-grid grid" aria-label="Investigation metrics">
        <MetricCard label="Total investigations" value={total} note="Reported by the API" icon={Database} tone="violet" />
        <MetricCard label="Received" value={countStatus('received')} note={scopeNote} icon={ArrowUpRight} tone="blue" />
        <MetricCard label="Awaiting media" value={countStatus('awaiting_media')} note={scopeNote} icon={Image} tone="amber" />
        <MetricCard label="Failed" value={countStatus('failed')} note={scopeNote} icon={AlertTriangle} tone="rose" />
      </section>

      {limited && <div className="data-limit-note"><Database size={14} /> Showing the latest {loadedCount} of {total.toLocaleString()} investigations. History pagination can load additional records.</div>}

      <section className="analytics-grid grid" aria-label="Investigation analytics">
        <InvestigationChart investigations={investigations} />
        <StatusDistribution investigations={investigations} />
      </section>

      <RecentInvestigations investigations={investigations} error={error} onRetry={() => void refresh()} />

      <footer className="dashboard-footer"><span>PS11 Multimodal Intelligence</span><span>AI analysis modules are not initialized in this milestone.</span></footer>
    </div>
  );
}
