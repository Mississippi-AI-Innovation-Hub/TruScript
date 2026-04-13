import { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  CheckCircle,
  XCircle,
  MessageSquare,
  Trash2,
  ChevronDown,
  ChevronUp,
  ClipboardList,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { transcriptsApi } from '../api';
import { useAuth } from '../context/AuthContext';
import { StatusBadge } from '../components/StatusBadge';
import { Spinner } from '../components/Spinner';
import { ConfirmModal } from '../components/ConfirmModal';
import type { VerificationStatus } from '../types';

function formatDateTime(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
}

function SectionBox({ title, icon: Icon, children, defaultOpen = true }: {
  title: string;
  icon: React.ElementType;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="card mb-4">
      <button
        className="flex items-center justify-between w-full text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flex items-center gap-2 font-semibold text-gray-800 text-sm">
          <Icon size={16} className="text-brand-500" />
          {title}
        </span>
        {open ? <ChevronUp size={16} className="text-gray-400" /> : <ChevronDown size={16} className="text-gray-400" />}
      </button>
      {open && <div className="mt-4">{children}</div>}
    </div>
  );
}

export function TranscriptDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user, isAdmin } = useAuth();

  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [annotationNote, setAnnotationNote] = useState('');
  const [reviewAction, setReviewAction] = useState<'confirm' | 'override' | null>(null);
  const [reviewFlagId, setReviewFlagId] = useState('');
  const [reviewJustification, setReviewJustification] = useState('');
  const [statusOverride, setStatusOverride] = useState<VerificationStatus | ''>('');

  const { data: transcript, isLoading } = useQuery({
    queryKey: ['transcript', id],
    queryFn: () => transcriptsApi.get(id!),
    enabled: !!id,
  });

  const { data: reviews } = useQuery({
    queryKey: ['reviews', id],
    queryFn: () => transcriptsApi.listReviews(id!),
    enabled: !!id,
  });

  const deleteMutation = useMutation({
    mutationFn: () => transcriptsApi.delete(id!),
    onSuccess: () => {
      toast.success('Verification record deleted.');
      navigate('/transcripts');
    },
    onError: () => toast.error('Failed to delete.'),
  });

  const annotationMutation = useMutation({
    mutationFn: () =>
      transcriptsApi.addAnnotation(id!, {
        staff_user_id: user!.sub,
        note: annotationNote,
      }),
    onSuccess: () => {
      toast.success('Annotation added.');
      setAnnotationNote('');
      queryClient.invalidateQueries({ queryKey: ['transcript', id] });
    },
    onError: () => toast.error('Failed to add annotation.'),
  });

  const reviewMutation = useMutation({
    mutationFn: () =>
      transcriptsApi.reviewFlag(id!, reviewFlagId, {
        action: reviewAction!,
        staff_user_id: user!.sub,
        justification: reviewAction === 'override' ? reviewJustification : null,
      }),
    onSuccess: () => {
      toast.success(`Flag ${reviewAction === 'confirm' ? 'confirmed' : 'overridden'}.`);
      setReviewAction(null);
      setReviewFlagId('');
      setReviewJustification('');
      queryClient.invalidateQueries({ queryKey: ['transcript', id] });
      queryClient.invalidateQueries({ queryKey: ['reviews', id] });
    },
    onError: () => toast.error('Failed to submit review.'),
  });

  const updateStatusMutation = useMutation({
    mutationFn: (status: VerificationStatus) =>
      transcriptsApi.update(id!, { status }),
    onSuccess: () => {
      toast.success('Status updated.');
      queryClient.invalidateQueries({ queryKey: ['transcript', id] });
    },
    onError: () => toast.error('Failed to update status.'),
  });

  if (isLoading) {
    return (
      <div className="flex justify-center items-center min-h-screen">
        <Spinner size={36} />
      </div>
    );
  }

  if (!transcript) {
    return (
      <div className="p-8 text-center text-gray-400">
        <p>Transcript not found.</p>
        <Link to="/transcripts" className="text-brand-500 text-sm mt-2 inline-block">← Back to list</Link>
      </div>
    );
  }

  const flags: Array<{ flag_id: string; type: string; description: string; severity?: string }> =
    (transcript.summary as Record<string, unknown>)?.flags as never ?? [];

  return (
    <div className="p-8 max-w-4xl">
      {/* Back + header */}
      <div className="mb-6">
        <Link
          to="/transcripts"
          className="inline-flex items-center gap-1 text-sm text-brand-500 hover:text-brand-700 mb-4"
        >
          <ArrowLeft size={14} /> Back to Verifications
        </Link>
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <h1 className="text-2xl font-bold text-brand-900">Verification Detail</h1>
            <p className="text-xs font-mono text-gray-400 mt-1">{transcript.verification_id}</p>
          </div>
          <div className="flex items-center gap-3">
            <StatusBadge status={transcript.status} />
            {isAdmin && (
              <button
                onClick={() => setShowDeleteModal(true)}
                className="btn-danger text-xs flex items-center gap-1 py-1.5 px-3"
              >
                <Trash2 size={14} /> Delete
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Core info */}
      <SectionBox title="Applicant Information" icon={ClipboardList}>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-gray-400 text-xs mb-0.5">Applicant ID</p>
            <p className="font-medium text-gray-800">{transcript.applicant_id}</p>
          </div>
          <div>
            <p className="text-gray-400 text-xs mb-0.5">Applicant Type</p>
            <p className="font-medium text-gray-800 capitalize">{transcript.applicant_type.replace('_', ' ')}</p>
          </div>
          <div>
            <p className="text-gray-400 text-xs mb-0.5">Assigned Staff</p>
            <p className="font-medium text-gray-800">{transcript.assigned_staff_id ?? '—'}</p>
          </div>
          <div>
            <p className="text-gray-400 text-xs mb-0.5">Created</p>
            <p className="font-medium text-gray-800">{formatDateTime(transcript.created_at)}</p>
          </div>
          <div>
            <p className="text-gray-400 text-xs mb-0.5">Last Updated</p>
            <p className="font-medium text-gray-800">{formatDateTime(transcript.updated_at)}</p>
          </div>
          {transcript.completed_at && (
            <div>
              <p className="text-gray-400 text-xs mb-0.5">Completed</p>
              <p className="font-medium text-gray-800">{formatDateTime(transcript.completed_at)}</p>
            </div>
          )}
        </div>

        {/* Status update */}
        <div className="mt-5 pt-5 border-t border-gray-100">
          <p className="text-xs font-semibold text-gray-500 uppercase mb-2">Update Status</p>
          <div className="flex items-center gap-2">
            <select
              className="input w-auto text-sm"
              value={statusOverride}
              onChange={(e) => setStatusOverride(e.target.value as VerificationStatus)}
            >
              <option value="">— select status —</option>
              {(['pending', 'in_progress', 'flagged', 'cleared', 'overridden'] as VerificationStatus[]).map(
                (s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>
              )}
            </select>
            <button
              disabled={!statusOverride || updateStatusMutation.isPending}
              onClick={() => statusOverride && updateStatusMutation.mutate(statusOverride as VerificationStatus)}
              className="btn-primary text-xs py-2 px-4"
            >
              {updateStatusMutation.isPending ? <Spinner size={14} /> : 'Apply'}
            </button>
          </div>
        </div>
      </SectionBox>

      {/* AI Summary */}
      <SectionBox title="AI Verification Summary" icon={ClipboardList} defaultOpen={true}>
        {!transcript.summary ? (
          <p className="text-sm text-gray-400 italic">No AI summary available yet.</p>
        ) : (
          <pre className="text-xs bg-gray-50 rounded-lg p-4 overflow-auto text-gray-700 border border-gray-100">
            {JSON.stringify(transcript.summary, null, 2)}
          </pre>
        )}
      </SectionBox>

      {/* Flags & Reviews */}
      {flags.length > 0 && (
        <SectionBox title="AI Flags" icon={XCircle}>
          <div className="space-y-3">
            {flags.map((flag) => {
              const reviewed = reviews?.find((r) => r.flag_id === flag.flag_id);
              return (
                <div key={flag.flag_id} className="border border-red-100 rounded-lg p-4 bg-red-50">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1">
                      <p className="text-sm font-semibold text-red-800">{flag.type}</p>
                      <p className="text-sm text-red-700 mt-0.5">{flag.description}</p>
                      {flag.severity && (
                        <p className="text-xs text-red-500 mt-1">Severity: {flag.severity}</p>
                      )}
                    </div>
                    {reviewed ? (
                      <span className={`text-xs font-semibold px-2 py-1 rounded-full ${
                        reviewed.action === 'confirm'
                          ? 'bg-red-100 text-red-700'
                          : 'bg-purple-100 text-purple-700'
                      }`}>
                        {reviewed.action === 'confirm' ? '✓ Confirmed' : '↩ Overridden'}
                      </span>
                    ) : (
                      <div className="flex gap-2 flex-shrink-0">
                        <button
                          onClick={() => {
                            setReviewFlagId(flag.flag_id);
                            setReviewAction('confirm');
                          }}
                          className="text-xs bg-red-600 text-white px-2.5 py-1 rounded-lg hover:bg-red-700 flex items-center gap-1"
                        >
                          <CheckCircle size={12} /> Confirm
                        </button>
                        <button
                          onClick={() => {
                            setReviewFlagId(flag.flag_id);
                            setReviewAction('override');
                          }}
                          className="text-xs bg-white border border-red-300 text-red-700 px-2.5 py-1 rounded-lg hover:bg-red-50 flex items-center gap-1"
                        >
                          <XCircle size={12} /> Override
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </SectionBox>
      )}

      {/* Inline flag review form */}
      {reviewAction && (
        <div className="card mb-4 border-brand-200">
          <h3 className="font-semibold text-gray-800 text-sm mb-3">
            {reviewAction === 'confirm' ? 'Confirm Flag' : 'Override Flag'}
          </h3>
          {reviewAction === 'override' && (
            <div className="mb-3">
              <label className="label">Justification (required)</label>
              <textarea
                className="input resize-none"
                rows={3}
                placeholder="Explain why this flag is being dismissed…"
                value={reviewJustification}
                onChange={(e) => setReviewJustification(e.target.value)}
              />
            </div>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => reviewMutation.mutate()}
              disabled={
                reviewMutation.isPending ||
                (reviewAction === 'override' && !reviewJustification.trim())
              }
              className="btn-primary text-xs py-2 px-4 flex items-center gap-1"
            >
              {reviewMutation.isPending ? <Spinner size={14} /> : null}
              Submit {reviewAction === 'confirm' ? 'Confirmation' : 'Override'}
            </button>
            <button
              onClick={() => { setReviewAction(null); setReviewJustification(''); }}
              className="btn-secondary text-xs py-2 px-4"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Annotations */}
      <SectionBox title="Annotations & Justifications" icon={MessageSquare}>
        {transcript.annotations.length === 0 ? (
          <p className="text-sm text-gray-400 italic mb-4">No annotations yet.</p>
        ) : (
          <div className="space-y-3 mb-4">
            {transcript.annotations.map((a, i) => {
              const ann = a as Record<string, unknown>;
              return (
                <div key={i} className="bg-blue-50 border border-blue-100 rounded-lg p-3 text-sm">
                  <p className="text-gray-800">{String(ann.note ?? '')}</p>
                  <div className="flex gap-3 text-xs text-gray-400 mt-1">
                    <span>{String(ann.staff_user_id ?? '')}</span>
                    {ann.created_at != null && <span>{formatDateTime(String(ann.created_at))}</span>}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <div>
          <label className="label">Add Annotation</label>
          <textarea
            className="input resize-none"
            rows={3}
            placeholder="Type your annotation or justification…"
            value={annotationNote}
            onChange={(e) => setAnnotationNote(e.target.value)}
          />
          <button
            disabled={!annotationNote.trim() || annotationMutation.isPending}
            onClick={() => annotationMutation.mutate()}
            className="btn-primary text-xs py-2 px-4 mt-2 flex items-center gap-1"
          >
            {annotationMutation.isPending ? <Spinner size={14} /> : <MessageSquare size={13} />}
            Add Annotation
          </button>
        </div>
      </SectionBox>

      {/* Audit trail */}
      {reviews && reviews.length > 0 && (
        <SectionBox title="Audit Trail — Flag Reviews" icon={ClipboardList} defaultOpen={false}>
          <div className="space-y-2">
            {reviews.map((r) => (
              <div key={r.review_id} className="text-sm flex items-start gap-3 p-3 rounded-lg bg-gray-50">
                <span className={`mt-0.5 px-2 py-0.5 rounded-full text-xs font-semibold ${
                  r.action === 'confirm'
                    ? 'bg-red-100 text-red-700'
                    : 'bg-purple-100 text-purple-700'
                }`}>
                  {r.action}
                </span>
                <div className="flex-1">
                  <p className="text-gray-700">
                    Flag <span className="font-mono text-xs">{r.flag_id}</span>
                    {r.justification ? ` — ${r.justification}` : ''}
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {r.staff_user_id} · {formatDateTime(r.created_at)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </SectionBox>
      )}

      {/* Documents */}
      {transcript.documents.length > 0 && (
        <SectionBox title="Documents" icon={ClipboardList} defaultOpen={false}>
          <pre className="text-xs bg-gray-50 rounded-lg p-4 overflow-auto text-gray-700 border border-gray-100">
            {JSON.stringify(transcript.documents, null, 2)}
          </pre>
        </SectionBox>
      )}

      {/* Delete modal */}
      {showDeleteModal && (
        <ConfirmModal
          title="Delete Verification Record"
          message={`Are you sure you want to permanently delete verification record for applicant "${transcript.applicant_id}"? This cannot be undone.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setShowDeleteModal(false)}
        />
      )}
    </div>
  );
}
