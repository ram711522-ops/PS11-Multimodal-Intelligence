import { apiClient } from './client';
import {
  parseProcessingJobResponse,
  parseProcessingResultsResponse,
} from '../types/processing';
import type {
  ProcessingJobCreateRequest,
  ProcessingJobResponse,
  ProcessingJobRetryRequest,
  ProcessingResultsResponse,
} from '../types/processing';

export const PROCESSING_API_AVAILABLE = false;
export const PROCESSING_API_UNAVAILABLE_REASON =
  'The current backend does not implement the proposed processing job, result, or retry endpoints.';

export class ProcessingApiUnavailableError extends Error {
  constructor() {
    super(PROCESSING_API_UNAVAILABLE_REASON);
    this.name = 'ProcessingApiUnavailableError';
  }
}

export interface ProcessingApi {
  readonly available: boolean;
  createJob(request: ProcessingJobCreateRequest): Promise<ProcessingJobResponse>;
  getJob(jobId: string): Promise<ProcessingJobResponse>;
  getResults(jobId: string): Promise<ProcessingResultsResponse>;
  retryJob(jobId: string, request: ProcessingJobRetryRequest): Promise<ProcessingJobResponse>;
}

export interface ProcessingHttpTransport {
  get(url: string): Promise<{ data: unknown }>;
  post(url: string, body: unknown): Promise<{ data: unknown }>;
}

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function assertUuid(value: string, field: string): void {
  if (!uuidPattern.test(value)) throw new TypeError(`${field} must be a UUID.`);
}

export function createProcessingApi(
  transport: ProcessingHttpTransport = apiClient,
  enabled = false,
): ProcessingApi {
  const assertEnabled = () => {
    if (!enabled) throw new ProcessingApiUnavailableError();
  };

  return {
    available: enabled,
    async createJob(request) {
      assertEnabled();
      assertUuid(request.investigation_id, 'investigation_id');
      assertUuid(request.media_id, 'media_id');
      const { data } = await transport.post('/api/v1/processing-jobs', request);
      return parseProcessingJobResponse(data);
    },
    async getJob(jobId) {
      assertEnabled();
      assertUuid(jobId, 'job_id');
      const { data } = await transport.get(`/api/v1/processing-jobs/${jobId}`);
      return parseProcessingJobResponse(data);
    },
    async getResults(jobId) {
      assertEnabled();
      assertUuid(jobId, 'job_id');
      const { data } = await transport.get(`/api/v1/processing-jobs/${jobId}/results`);
      return parseProcessingResultsResponse(data);
    },
    async retryJob(jobId, request) {
      assertEnabled();
      assertUuid(jobId, 'job_id');
      if (request.retry_failed !== true) {
        throw new TypeError('retry_failed must be explicitly true.');
      }
      const { data } = await transport.post(`/api/v1/processing-jobs/${jobId}/retry`, request);
      return parseProcessingJobResponse(data);
    },
  };
}

// The proposed route client exists for contract-level tests and future wiring,
// but this default service remains disabled until the backend implements it.
export const processingApi = createProcessingApi(apiClient, PROCESSING_API_AVAILABLE);
