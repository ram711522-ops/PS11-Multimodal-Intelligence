import { Bell, ChevronRight, Menu, Search, UserRound } from 'lucide-react';

interface HeaderProps {
  title: string;
  onOpenNavigation: () => void;
}

export function Header({ title, onOpenNavigation }: HeaderProps) {
  return (
    <header className="topbar flex items-center justify-between">
      <div className="topbar-heading">
        <button className="icon-button mobile-menu-button" onClick={onOpenNavigation} aria-label="Open navigation menu">
          <Menu size={19} />
        </button>
        <div>
          <div className="breadcrumb"><span>PS11</span><ChevronRight size={12} /><span>Workspace</span></div>
          <h1>{title}</h1>
        </div>
      </div>
      <div className="topbar-actions">
        <label className="search-control">
          <Search size={16} />
          <input aria-label="Search investigations" placeholder="Search · coming soon" disabled />
        </label>
        <button className="icon-button notification-button" aria-label="Notifications coming soon" title="Notifications coming soon" disabled>
          <Bell size={18} />
        </button>
        <div className="profile-divider" />
        <div className="profile-chip" aria-label="Signed-in identity is not configured">
          <div className="profile-avatar"><UserRound size={17} /></div>
          <div className="profile-name"><strong>Analyst</strong><span>Local workspace</span></div>
        </div>
      </div>
    </header>
  );
}
