import apiClient from './client';
import type {
  TranscriptCreateRequest,
  TranscriptUpdateRequest,
  TranscriptResponse,
  TranscriptListResponse,
  FlagReviewRequest,
  FlagReviewResponse,
  AnnotationRequest,
  WorkflowTranscriptResponse,
} from '../types';

export const transcriptsApi = {
  // ─── CRUD ────────────────────────────────────────────────────────────────

  create: (data: TranscriptCreateRequest) =>
    apiClient.post<TranscriptResponse>('/api/v1/transcripts', data).then((r) => r.data),

  list: (skip = 0, limit = 20) =>
    apiClient
      .get<TranscriptListResponse>('/api/v1/transcripts', { params: { skip, limit } })
      .then((r) => r.data),

  get: (verificationId: string) =>
    apiClient
      .get<TranscriptResponse>(`/api/v1/transcripts/${verificationId}`)
      .then((r) => r.data),

  update: (verificationId: string, data: TranscriptUpdateRequest) =>
    apiClient
      .patch<TranscriptResponse>(`/api/v1/transcripts/${verificationId}`, data)
      .then((r) => r.data),

  delete: (verificationId: string) =>
    apiClient
      .delete<Record<string, unknown>>(`/api/v1/transcripts/${verificationId}`)
      .then((r) => r.data),

  bulkDelete: (verificationIds: string[]) =>
    apiClient
      .delete<{ deleted: string[]; not_found: string[]; count: number }>(
        '/api/v1/transcripts',
        { data: { verification_ids: verificationIds } }
      )
      .then((r) => r.data),

  // ─── Flag Reviews ─────────────────────────────────────────────────────────

  reviewFlag: (verificationId: string, flagId: string, data: FlagReviewRequest) =>
    apiClient
      .post<FlagReviewResponse>(
        `/api/v1/transcripts/${verificationId}/flags/${flagId}/reviews`,
        data
      )
      .then((r) => r.data),

  listReviews: (verificationId: string) =>
    apiClient
      .get<FlagReviewResponse[]>(`/api/v1/transcripts/${verificationId}/reviews`)
      .then((r) => r.data),

  // ─── Retry pipeline ──────────────────────────────────────────────────────

  retry: (verificationId: string) =>
    apiClient
      .post<TranscriptResponse>(`/api/v1/transcripts/${verificationId}/retry`)
      .then((r) => r.data),

  // ─── Annotations ─────────────────────────────────────────────────────────

  addAnnotation: (verificationId: string, data: AnnotationRequest) =>
    apiClient
      .post<WorkflowTranscriptResponse>(
        `/api/v1/transcripts/${verificationId}/annotations`,
        data
      )
      .then((r) => r.data),
};
