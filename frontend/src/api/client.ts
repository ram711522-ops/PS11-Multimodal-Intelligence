import axios from 'axios';

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();

export const apiClient = axios.create({
  baseURL: configuredBaseUrl || undefined,
  timeout: 10_000,
  headers: { Accept: 'application/json' },
});
