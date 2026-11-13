import { ArrowLeft, Clock3, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';

export function ComingSoon({ title }: { title: string }) {
  return (
    <section className="coming-page">
      <div className="coming-card">
        <div className="coming-icon"><Sparkles size={23} /></div>
        <span className="coming-label"><Clock3 size={13} /> COMING SOON</span>
        <h2>{title}</h2>
        <p>This workspace is not available in the current milestone. No analysis or submission functionality is connected here yet.</p>
        <Link className="button button--secondary" to="/"><ArrowLeft size={15} /> Return to overview</Link>
      </div>
    </section>
  );
}
