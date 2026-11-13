export const PROCESSING_API_VERSION = '1.0' as const;

export type ProcessingStatus =
  | 'received'
  | 'queued'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'skipped';

export interface ProcessingFailure {
  code: string;
  message: string;
  occurred_at: string;
}

export interface ProcessingJob {
  job_id: string;
  investigation_id: string;
  media_id: string;
  processing_status: ProcessingStatus;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  failure: ProcessingFailure | null;
}

export interface ProcessingResult {
  result_id: string;
  job_id: string;
  media_id: string;
  algorithm: string;
  algorithm_version: string;
  result_type: string;
  result_data: Record<string, unknown>;
  created_at: string;
}

export interface ProcessingJobCreateRequest {
  investigation_id: string;
  media_id: string;
}

export interface ProcessingJobRetryRequest {
  retry_failed: true;
}

export interface ProcessingJobResponse {
  api_version: typeof PROCESSING_API_VERSION;
  job: ProcessingJob;
}

export interface ProcessingResultsResponse {
  api_version: typeof PROCESSING_API_VERSION;
  job_id: string;
  items: ProcessingResult[];
  total: number;
}

export interface ProcessingApiErrorResponse {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const statuses = new Set<ProcessingStatus>([
  'received', 'queued', 'processing', 'completed', 'failed', 'skipped',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isUuid(value: unknown): value is string {
  return typeof value === 'string' && uuidPattern.test(value);
}

function isUtcTimestamp(value: unknown): value is string {
  return typeof value === 'string'
    && /(?:Z|\+00:00)$/.test(value)
    && Number.isFinite(Date.parse(value));
}

function parseFailure(value: unknown): ProcessingFailure | null {
  if (value === null) return null;
  if (!isRecord(value)
    || typeof value.code !== 'string'
    || typeof value.message !== 'string'
    || !isUtcTimestamp(value.occurred_at)) {
    throw new TypeError('Processing failure does not match API v1.0.');
  }
  return { code: value.code, message: value.message, occurred_at: value.occurred_at };
}

export function parseProcessingJobResponse(value: unknown): ProcessingJobResponse {
  if (!isRecord(value) || value.api_version !== PROCESSING_API_VERSION || !isRecord(value.job)) {
    throw new TypeError('Processing job response does not match API v1.0.');
  }
  const job = value.job;
  const nullableUtc = (timestamp: unknown) => timestamp === null || isUtcTimestamp(timestamp);
  if (!isUuid(job.job_id)
    || !isUuid(job.investigation_id)
    || !isUuid(job.media_id)
    || typeof job.processing_status !== 'string'
    || !statuses.has(job.processing_status as ProcessingStatus)
    || !isUtcTimestamp(job.created_at)
    || !nullableUtc(job.started_at)
    || !nullableUtc(job.completed_at)) {
    throw new TypeError('Processing job fields do not match API v1.0.');
  }
  return {
    api_version: PROCESSING_API_VERSION,
    job: {
      job_id: job.job_id,
      investigation_id: job.investigation_id,
      media_id: job.media_id,
      processing_status: job.processing_status as ProcessingStatus,
      created_at: job.created_at,
      started_at: job.started_at as string | null,
      completed_at: job.completed_at as string | null,
      failure: parseFailure(job.failure),
    },
  };
}

export function parseProcessingResultsResponse(value: unknown): ProcessingResultsResponse {
  if (!isRecord(value)
    || value.api_version !== PROCESSING_API_VERSION
    || !isUuid(value.job_id)
    || !Array.isArray(value.items)
    || !Number.isSafeInteger(value.total)
    || (value.total as number) < value.items.length) {
    throw new TypeError('Processing results response does not match API v1.0.');
  }
  const items = value.items.map((item): ProcessingResult => {
    if (!isRecord(item)
      || !isUuid(item.result_id)
      || item.job_id !== value.job_id
      || !isUuid(item.media_id)
      || typeof item.algorithm !== 'string'
      || typeof item.algorithm_version !== 'string'
      || typeof item.result_type !== 'string'
      || !isRecord(item.result_data)
      || !isUtcTimestamp(item.created_at)) {
      throw new TypeError('Processing result item does not match API v1.0.');
    }
    return {
      result_id: item.result_id,
      job_id: item.job_id as string,
      media_id: item.media_id,
      algorithm: item.algorithm,
      algorithm_version: item.algorithm_version,
      result_type: item.result_type,
      result_data: item.result_data,
      created_at: item.created_at,
    };
  });
  return {
    api_version: PROCESSING_API_VERSION,
    job_id: value.job_id,
    items,
    total: value.total as number,
  };
}

export function isProcessingApiError(value: unknown): value is ProcessingApiErrorResponse {
  return isRecord(value)
    && isRecord(value.error)
    && typeof value.error.code === 'string'
    && typeof value.error.message === 'string'
    && isRecord(value.error.details);
}
