import { ArrowLeft, CircleHelp } from 'lucide-react';
import { Link } from 'react-router-dom';

export function NotFound() {
  return (
    <section className="coming-page">
      <div className="coming-card">
        <div className="coming-icon"><CircleHelp size={23} /></div>
        <span className="coming-label">404 · NOT FOUND</span>
        <h2>That page isn’t here</h2>
        <p>The route may have changed, or the page has not been added to this workspace.</p>
        <Link className="button button--secondary" to="/"><ArrowLeft size={15} /> Return to overview</Link>
      </div>
    </section>
  );
}
