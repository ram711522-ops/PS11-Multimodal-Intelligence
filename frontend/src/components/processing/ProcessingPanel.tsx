import { AlertCircle, Check, RefreshCw, Timer, Workflow } from 'lucide-react';
import type { ProcessingJob, ProcessingResult, ProcessingStatus } from '../../types/processing';

interface ProcessingPanelProps {
  apiAvailable?: boolean;
  job?: ProcessingJob | null;
  results?: readonly ProcessingResult[] | null;
  isLoading?: boolean;
  error?: string | null;
  isRetrying?: boolean;
  onReload?: () => void;
  onRetry?: () => void;
}

const lifecycle: readonly ProcessingStatus[] = ['received', 'queued', 'processing', 'completed'];

function statusLabel(status: ProcessingStatus): string {
  return status.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function dateLabel(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—';
}

function ProgressTimeline({ status }: { status: ProcessingStatus }) {
  const states = status === 'failed'
    ? [...lifecycle.slice(0, 3), 'failed' as const]
    : status === 'skipped'
      ? ['skipped' as const]
      : lifecycle;
  const currentIndex = states.indexOf(status);

  return (
    <ol className="processing-timeline" aria-label="Processing lifecycle progress">
      {states.map((step, index) => {
        const isCurrent = index === currentIndex;
        const reached = !isCurrent && (currentIndex < 0 || index < currentIndex);
        return (
          <li className="processing-timeline-step" data-state={isCurrent ? 'current' : reached ? 'reached' : 'pending'} key={step}>
            <span className="processing-timeline-marker" aria-hidden="true">
              {reached ? <Check size={12} /> : isCurrent ? <i /> : null}
            </span>
            <span>{statusLabel(step)}</span>
          </li>
        );
      })}
    </ol>
  );
}

export function ProcessingPanel({
  apiAvailable = false,
  job = null,
  results = null,
  isLoading = false,
  error = null,
  isRetrying = false,
  onReload,
  onRetry,
}: ProcessingPanelProps) {
  return (
    <section className="panel processing-panel" aria-labelledby="processing-panel-heading">
      <div className="panel-heading processing-heading">
        <div><div className="eyebrow">MEDIA INTELLIGENCE</div><h2 id="processing-panel-heading">Processing</h2></div>
        {job && <span className={`processing-status-badge processing-status-badge--${job.processing_status}`}>
          <i aria-hidden="true" />{statusLabel(job.processing_status)}
        </span>}
      </div>

      {isLoading ? (
        <div className="processing-state" role="status" aria-label="Loading processing information">
          <span className="sr-only">Loading processing information</span>
          <i className="processing-skeleton processing-skeleton--wide" />
          <i className="processing-skeleton" />
          <i className="processing-skeleton processing-skeleton--wide" />
        </div>
      ) : !apiAvailable ? (
        <div className="processing-dependency" role="status">
          <Workflow size={18} />
          <div><strong>Processing API unavailable</strong><p>The current backend does not expose processing jobs, results, or retry endpoints. No processing data is being shown.</p></div>
        </div>
      ) : error ? (
        <div className="processing-error" role="alert">
          <AlertCircle size={17} />
          <div><strong>Processing information unavailable</strong><p>{error}</p></div>
          {onReload && <button className="button button--secondary" onClick={onReload}><RefreshCw size={13} /> Retry load</button>}
        </div>
      ) : !job ? (
        <div className="processing-empty"><Timer size={17} /><p>No processing job is available for this investigation.</p></div>
      ) : (
        <div className="processing-body">
          <div className="processing-identifiers">
            <div><span>Job</span><code title={job.job_id}>{job.job_id}</code></div>
            <div><span>Media</span><code title={job.media_id}>{job.media_id}</code></div>
          </div>
          <ProgressTimeline status={job.processing_status} />
          <div className="processing-times">
            <span>Created <time dateTime={job.created_at}>{dateLabel(job.created_at)}</time></span>
            {job.started_at && <span>Started <time dateTime={job.started_at}>{dateLabel(job.started_at)}</time></span>}
            {job.completed_at && <span>Finished <time dateTime={job.completed_at}>{dateLabel(job.completed_at)}</time></span>}
          </div>

          {job.failure && (
            <div className="processing-failure" role="alert">
              <strong>{job.failure.code}</strong><p>{job.failure.message}</p>
              <time dateTime={job.failure.occurred_at}>{dateLabel(job.failure.occurred_at)}</time>
            </div>
          )}

          {job.processing_status === 'failed' && onRetry && (
            <button className="button button--secondary processing-retry" onClick={onRetry} disabled={isRetrying}>
              <RefreshCw size={13} className={isRetrying ? 'spin' : ''} />{isRetrying ? 'Retrying' : 'Retry processing'}
            </button>
          )}

          <div className="processing-results">
            <h3>Results</h3>
            {results === null ? (
              <p className="processing-muted">Results have not been retrieved.</p>
            ) : results.length === 0 ? (
              <p className="processing-muted">No processing results have been returned.</p>
            ) : (
              <ul>
                {results.map((result) => (
                  <li className="processing-result" key={result.result_id}>
                    <div className="processing-result-heading">
                      <strong>{result.result_type.replaceAll('_', ' ')}</strong>
                      <span>{result.algorithm} · {result.algorithm_version}</span>
                    </div>
                    <time dateTime={result.created_at}>{dateLabel(result.created_at)}</time>
                    <pre>{JSON.stringify(result.result_data, null, 2)}</pre>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
