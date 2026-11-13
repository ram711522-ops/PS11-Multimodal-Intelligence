export type InputType = 'text' | 'image' | 'text_image';

export interface Claim {
  text: string | null;
  language: string | null;
  normalized_text: string | null;
}

export interface Investigation {
  investigation_id: string;
  input_type: InputType;
  status: string;
  claim: Claim | null;
  source_reference: string | null;
  created_at: string;
  updated_at: string;
  media: unknown[];
  latest_job: unknown | null;
  latest_result: unknown | null;
  failure: unknown | null;
}

export interface InvestigationSummary {
  investigation_id: string;
  input_type: InputType;
  status: string;
  claim: Claim | null;
  source_reference: string | null;
  created_at: string;
  updated_at: string;
}

export interface InvestigationHistory {
  items: InvestigationSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface HealthResponse {
  status: string;
  application: string;
  database: string;
  ai_modules: string;
}
