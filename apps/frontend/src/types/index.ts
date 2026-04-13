// ─── Auth ────────────────────────────────────────────────────────────────────

export interface LoginRequest {
  username: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  refresh_expires_in: number;
}

export interface LogoutRequest {
  refresh_token: string;
}

export interface MeResponse {
  sub: string;
  username: string;
  email: string | null;
  name: string | null;
  roles: string[];
}

// ─── Users ───────────────────────────────────────────────────────────────────

export type UserRole = 'msbn-staff' | 'msbn-admin';

export interface CreateUserRequest {
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  password: string;
  role?: UserRole;
}

export interface UserResponse {
  id: string;
  username: string;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  enabled: boolean;
  roles: string[];
}

// ─── Transcripts ─────────────────────────────────────────────────────────────

export type ApplicantType = 'first_time' | 'endorsement';

export type VerificationStatus =
  | 'pending'
  | 'in_progress'
  | 'flagged'
  | 'cleared'
  | 'overridden';

export interface TranscriptCreateRequest {
  applicant_id: string;
  applicant_type: ApplicantType;
  assigned_staff_id?: string | null;
}

export interface TranscriptUpdateRequest {
  assigned_staff_id?: string | null;
  status?: VerificationStatus | null;
  summary?: Record<string, unknown> | null;
  annotations_to_append?: Record<string, unknown>[];
  documents_to_append?: Record<string, unknown>[];
  completed_at?: string | null;
}

export interface TranscriptResponse {
  verification_id: string;
  applicant_id: string;
  applicant_type: string;
  status: VerificationStatus;
  assigned_staff_id: string | null;
  documents: Record<string, unknown>[];
  summary: Record<string, unknown> | null;
  annotations: Record<string, unknown>[];
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface TranscriptListResponse {
  items: TranscriptResponse[];
  total: number;
  skip: number;
  limit: number;
}

// ─── Flag Reviews ─────────────────────────────────────────────────────────────

export type FlagReviewAction = 'confirm' | 'override';

export interface FlagReviewRequest {
  action: FlagReviewAction;
  staff_user_id: string;
  justification?: string | null;
  note?: string | null;
}

export interface FlagReviewResponse {
  review_id: string;
  verification_id: string;
  flag_id: string;
  action: string;
  staff_user_id: string;
  justification: string | null;
  note: string | null;
  created_at: string;
}

// ─── Annotations ─────────────────────────────────────────────────────────────

export interface AnnotationRequest {
  staff_user_id: string;
  note: string;
  overrides_flag_id?: string | null;
}

export interface WorkflowTranscriptResponse {
  verification_id: string;
  applicant_id: string;
  status: string;
  annotations: Record<string, unknown>[];
  updated_at: string;
}

// ─── API Error ────────────────────────────────────────────────────────────────

export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export interface HTTPValidationError {
  detail: ValidationError[];
}
