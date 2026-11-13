import type { InvestigationSummary } from '../types/investigation';

const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
});

const dateFormatter = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });

export function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unknown date' : dateTimeFormatter.format(date);
}

export function formatDate(value: Date): string {
  return dateFormatter.format(value);
}

export function shortId(value: string): string {
  return `${value.slice(0, 8)}…${value.slice(-4)}`;
}

export function displayClaim(investigation: InvestigationSummary): string {
  if (investigation.input_type === 'image') return 'Image investigation';
  return investigation.claim?.text?.trim() || 'Claim unavailable';
}

export function titleCaseStatus(status: string): string {
  return status.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}
