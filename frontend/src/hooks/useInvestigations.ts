import { useCallback, useEffect, useRef, useState } from 'react';
import { getHealth, getInvestigationHistory } from '../api/investigations';
import type { HealthResponse, InvestigationHistory } from '../types/investigation';

export interface DashboardData {
  health: HealthResponse | null;
  history: InvestigationHistory;
}

export interface InvestigationsState {
  data: DashboardData | null;
  isLoading: boolean;
  isRefreshing: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

let inFlightRequest: Promise<DashboardData> | null = null;

function fetchDashboardData(): Promise<DashboardData> {
  if (!inFlightRequest) {
    inFlightRequest = Promise.all([
      getHealth().catch(() => null),
      getInvestigationHistory(100, 0),
    ]).then(([health, history]) => ({ health, history }))
      .finally(() => { inFlightRequest = null; });
  }
  return inFlightRequest;
}

export function useInvestigations(): InvestigationsState {
  const [data, setData] = useState<DashboardData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasData = useRef(false);

  const refresh = useCallback(async () => {
    setError(null);
    if (hasData.current) setIsRefreshing(true);
    else setIsLoading(true);
    try {
      const result = await fetchDashboardData();
      hasData.current = true;
      setData(result);
    } catch {
      setError('We couldn’t load investigations from the API. Check that the backend is running and try again.');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  return { data, isLoading, isRefreshing, error, refresh };
}
