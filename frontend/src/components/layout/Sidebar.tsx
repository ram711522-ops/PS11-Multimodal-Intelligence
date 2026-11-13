import {
  ChevronLeft, ChevronRight, Compass, FileSearch2,
  Image, Layers3, Network, Plus, Route, Settings, ShieldCheck, X,
} from 'lucide-react';
import { NavLink } from 'react-router-dom';

const navigation = [
  { label: 'Overview', path: '/', icon: Compass, end: true },
  { label: 'Investigations', path: '/investigations', icon: FileSearch2 },
  { label: 'New Investigation', path: '/new-investigation', icon: Plus },
  { label: 'Evidence Explorer', path: '/evidence-explorer', icon: Layers3 },
  { label: 'Media Intelligence', path: '/media-intelligence', icon: Image },
  { label: 'Coordination Analysis', path: '/coordination-analysis', icon: Network },
  { label: 'Propagation Timeline', path: '/propagation-timeline', icon: Route },
  { label: 'Settings', path: '/settings', icon: Settings },
];

interface SidebarProps {
  collapsed: boolean;
  mobileOpen: boolean;
  apiAvailable: boolean | null;
  onToggleCollapse: () => void;
  onCloseMobile: () => void;
}

export function Sidebar({ collapsed, mobileOpen, apiAvailable, onToggleCollapse, onCloseMobile }: SidebarProps) {
  return (
    <>
      {mobileOpen && <button className="mobile-scrim" aria-label="Close navigation menu" onClick={onCloseMobile} />}
      <aside className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''} ${mobileOpen ? 'sidebar--mobile-open' : ''}`}>
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true"><span>PS</span><i>11</i></div>
          <div className="brand-copy"><strong>PS11</strong><span>Multimodal Intelligence</span></div>
          <button className="icon-button sidebar-mobile-close" onClick={onCloseMobile} aria-label="Close navigation">
            <X size={18} />
          </button>
        </div>

        <div className="workspace-label"><span>WORKSPACE</span><span className="workspace-dot" /></div>
        <nav className="sidebar-nav flex flex-col" aria-label="Main navigation">
          {navigation.map(({ label, path, icon: Icon, end }) => (
            <NavLink
              key={path}
              to={path}
              end={end}
              title={collapsed ? label : undefined}
              aria-label={label}
              onClick={onCloseMobile}
              className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`}
            >
              <Icon size={18} strokeWidth={1.8} />
              <span className="nav-link-label">{label}</span>
              {label !== 'Overview' && (
                <span className="nav-coming">SOON</span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="api-status">
            <span className={`api-status-dot ${apiAvailable === false ? 'api-status-dot--offline' : ''}`} />
            <div className="api-status-copy">
              <strong>{apiAvailable === null ? 'API status unknown' : apiAvailable ? 'API connected' : 'API unavailable'}</strong>
              <span>{apiAvailable ? 'Phase 2 services' : 'Check local backend'}</span>
            </div>
          </div>
          <div className="sidebar-footnote"><ShieldCheck size={14} /><span>Investigation workspace</span></div>
        </div>

        <button
          className="collapse-button"
          onClick={onToggleCollapse}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-expanded={!collapsed}
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          <span>{collapsed ? 'Expand' : 'Collapse'}</span>
        </button>
      </aside>
    </>
  );
}
