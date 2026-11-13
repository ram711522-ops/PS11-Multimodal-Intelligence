import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const frontendRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
let vite;
let ProcessingPanel;
let processingTypes;
let processingApiModule;

before(async () => {
  vite = await createServer({
    configFile: resolve(frontendRoot, 'vite.config.ts'),
    root: frontendRoot,
    logLevel: 'error',
    server: { middlewareMode: true },
    appType: 'custom',
  });
  ({ ProcessingPanel } = await vite.ssrLoadModule('/src/components/processing/ProcessingPanel.tsx'));
  processingTypes = await vite.ssrLoadModule('/src/types/processing.ts');
  processingApiModule = await vite.ssrLoadModule('/src/api/processing.ts');
});

after(async () => {
  await vite?.close();
});

const job = {
  job_id: '96d4f41c-6ea2-4cf1-9b16-f28b0e55eb02',
  investigation_id: '255424f1-2e46-48b0-a457-53a5f0b26e60',
  media_id: '7b28b64d-7a3f-4c88-a648-61e881ab15b2',
  processing_status: 'completed',
  created_at: '2026-11-13T09:30:00Z',
  started_at: '2026-11-13T09:30:01Z',
  completed_at: '2026-11-13T09:30:02Z',
  failure: null,
};

const result = {
  result_id: '8a1dd1c8-8257-4e5a-bd85-20ffcf90e08d',
  job_id: job.job_id,
  media_id: job.media_id,
  algorithm: 'phash',
  algorithm_version: 'dct-64-v1',
  result_type: 'perceptual_hash',
  result_data: { hash: '0123456789abcdef' },
  created_at: '2026-11-13T09:30:02Z',
};

function render(props = {}) {
  return renderToStaticMarkup(createElement(ProcessingPanel, props));
}

test('unavailable integration reports dependency and renders no processing data', () => {
  const html = render();
  assert.match(html, /Processing API unavailable/);
  assert.match(html, /No processing data is being shown/);
  assert.doesNotMatch(html, /Retry processing|perceptual hash/);
});

test('renders server-provided job lifecycle and result payload', () => {
  const html = render({ apiAvailable: true, job, results: [result] });
  assert.match(html, /Completed/);
  assert.match(html, /Processing lifecycle progress/);
  assert.match(html, /dct-64-v1/);
  assert.match(html, /0123456789abcdef/);
  assert.match(html, /96d4f41c-6ea2-4cf1-9b16-f28b0e55eb02/);
});

test('renders actual failure and offers retry only when a handler is supplied', () => {
  const failedJob = {
    ...job,
    processing_status: 'failed',
    failure: {
      code: 'INVALID_IMAGE',
      message: 'Image content could not be validated.',
      occurred_at: '2026-11-13T09:30:02Z',
    },
  };
  const withRetry = render({ apiAvailable: true, job: failedJob, results: [], onRetry() {} });
  const withoutRetry = render({ apiAvailable: true, job: failedJob, results: [] });
  assert.match(withRetry, /INVALID_IMAGE/);
  assert.match(withRetry, /Retry processing/);
  assert.match(withRetry, /No processing results have been returned/);
  assert.doesNotMatch(withoutRetry, /Retry processing/);
});

test('loading and retrieval errors have explicit states', () => {
  assert.match(render({ apiAvailable: true, isLoading: true }), /Loading processing information/);
  assert.match(render({ apiAvailable: true, error: 'Job status could not be loaded.', onReload() {} }), /Retry load/);
});

test('runtime schemas accept versioned UTC payloads and reject malformed ones', () => {
  const validJob = { api_version: '1.0', job };
  assert.equal(processingTypes.parseProcessingJobResponse(validJob).job.job_id, job.job_id);
  assert.throws(() => processingTypes.parseProcessingJobResponse({
    api_version: '2.0', job,
  }), /API v1.0/);
  assert.throws(() => processingTypes.parseProcessingJobResponse({
    api_version: '1.0', job: { ...job, created_at: '2026-11-13T09:30:00' },
  }), /API v1.0/);

  const results = { api_version: '1.0', job_id: job.job_id, items: [result], total: 1 };
  assert.equal(processingTypes.parseProcessingResultsResponse(results).items[0].algorithm_version, 'dct-64-v1');
  assert.throws(() => processingTypes.parseProcessingResultsResponse({
    ...results, items: [{ ...result, job_id: '96d4f41c-6ea2-4cf1-9b16-f28b0e55eb03' }],
  }), /API v1.0/);
});

test('processing API client stays disabled by default and maps versioned routes when enabled', async () => {
  const disabled = processingApiModule.createProcessingApi({
    async get() { throw new Error('must not call transport'); },
    async post() { throw new Error('must not call transport'); },
  });
  assert.equal(disabled.available, false);
  await assert.rejects(
    disabled.createJob({ investigation_id: job.investigation_id, media_id: job.media_id }),
    /does not implement/,
  );

  const calls = [];
  const transport = {
    async get(url) {
      calls.push(['get', url]);
      return { data: url.endsWith('/results')
        ? { api_version: '1.0', job_id: job.job_id, items: [result], total: 1 }
        : { api_version: '1.0', job } };
    },
    async post(url, body) {
      calls.push(['post', url, body]);
      return { data: { api_version: '1.0', job: { ...job, processing_status: 'queued' } } };
    },
  };
  const api = processingApiModule.createProcessingApi(transport, true);
  await api.createJob({ investigation_id: job.investigation_id, media_id: job.media_id });
  await api.getJob(job.job_id);
  await api.getResults(job.job_id);
  await api.retryJob(job.job_id, { retry_failed: true });
  assert.deepEqual(calls.map(([method, url]) => [method, url]), [
    ['post', '/api/v1/processing-jobs'],
    ['get', `/api/v1/processing-jobs/${job.job_id}`],
    ['get', `/api/v1/processing-jobs/${job.job_id}/results`],
    ['post', `/api/v1/processing-jobs/${job.job_id}/retry`],
  ]);
  await assert.rejects(api.retryJob(job.job_id, { retry_failed: false }), /explicitly true/);
});
