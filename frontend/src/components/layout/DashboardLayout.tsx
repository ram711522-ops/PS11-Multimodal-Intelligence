import { useMemo, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Header } from './Header';
import { Sidebar } from './Sidebar';
import { useInvestigations } from '../../hooks/useInvestigations';

const pageTitles: Record<string, string> = {
  '/': 'Overview',
  '/investigations': 'Investigations',
  '/new-investigation': 'New investigation',
  '/evidence-explorer': 'Evidence explorer',
  '/media-intelligence': 'Media intelligence',
  '/coordination-analysis': 'Coordination analysis',
  '/propagation-timeline': 'Propagation timeline',
  '/settings': 'Settings',
};

export function DashboardLayout() {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const dashboardState = useInvestigations();
  const title = useMemo(() => pageTitles[location.pathname] ?? 'Overview', [location.pathname]);
  const apiAvailable = dashboardState.data
    ? dashboardState.data.health?.status === 'ok' && dashboardState.data.health.database === 'ok'
    : dashboardState.error ? false : null;

  return (
    <div className={`app-shell ${collapsed ? 'app-shell--collapsed' : ''}`}>
      <Sidebar
        collapsed={collapsed}
        mobileOpen={mobileOpen}
        apiAvailable={apiAvailable}
        onToggleCollapse={() => setCollapsed((value) => !value)}
        onCloseMobile={() => setMobileOpen(false)}
      />
      <div className="main-column">
        <Header title={title} onOpenNavigation={() => setMobileOpen(true)} />
        <main className="page-content"><Outlet context={dashboardState} /></main>
      </div>
    </div>
  );
}
