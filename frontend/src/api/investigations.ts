import { apiClient } from './client';
import type { HealthResponse, InvestigationHistory } from '../types/investigation';

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>('/health');
  return data;
}

export async function getInvestigationHistory(
  limit = 100,
  offset = 0,
): Promise<InvestigationHistory> {
  const { data } = await apiClient.get<InvestigationHistory>('/api/investigations', {
    params: { limit, offset },
  });
  return data;
}
