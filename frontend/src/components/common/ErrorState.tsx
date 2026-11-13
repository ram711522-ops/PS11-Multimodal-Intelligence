import { AlertCircle, RefreshCw } from 'lucide-react';

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="state-panel state-panel--error" role="alert">
      <div className="state-icon state-icon--error"><AlertCircle size={20} /></div>
      <div className="state-copy"><strong>We couldn’t load the dashboard</strong><p>{message}</p></div>
      <button className="button button--secondary" onClick={onRetry}><RefreshCw size={15} /> Try again</button>
    </div>
  );
}
