import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router-dom';

const DashboardLayout = lazy(() => import('./components/layout/DashboardLayout').then((module) => ({ default: module.DashboardLayout })));
const Dashboard = lazy(() => import('./pages/Dashboard').then((module) => ({ default: module.Dashboard })));
const ComingSoon = lazy(() => import('./pages/ComingSoon').then((module) => ({ default: module.ComingSoon })));
const NotFound = lazy(() => import('./pages/NotFound').then((module) => ({ default: module.NotFound })));

export default function App() {
  return (
    <Suspense fallback={<div className="page-content" role="status">Loading workspace…</div>}>
      <Routes>
        <Route element={<DashboardLayout />}>
          <Route index element={<Dashboard />} />
          <Route path="investigations" element={<ComingSoon title="Investigations" />} />
          <Route path="new-investigation" element={<ComingSoon title="New investigation" />} />
          <Route path="evidence-explorer" element={<ComingSoon title="Evidence explorer" />} />
          <Route path="media-intelligence" element={<ComingSoon title="Media intelligence" />} />
          <Route path="coordination-analysis" element={<ComingSoon title="Coordination analysis" />} />
          <Route path="propagation-timeline" element={<ComingSoon title="Propagation timeline" />} />
          <Route path="settings" element={<ComingSoon title="Settings" />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
