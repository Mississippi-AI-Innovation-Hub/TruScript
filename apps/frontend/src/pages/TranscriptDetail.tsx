import React, { useEffect, useMemo, useState } from 'react';
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
  FileText,
  Image,
  File,
  User,
  Download,
  Eye,
  X,
  ZoomIn,
  ZoomOut,
  RotateCw,
  RefreshCcw,
  Loader2,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { transcriptsApi, authApi, apiClient } from '../api';
import { useAuth } from '../context/AuthContext';
import { StatusBadge } from '../components/StatusBadge';
import { Spinner } from '../components/Spinner';
import { ConfirmModal } from '../components/ConfirmModal';
import type { UserResponse, VerificationStatus } from '../types';

function formatDateTime(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
}

function formatBytes(bytes: number): string {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function buildStaffMap(staff: UserResponse[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const s of staff) {
    const name = s.first_name && s.last_name
      ? `${s.first_name} ${s.last_name}`
      : s.username;
    map.set(s.id, name);
  }
  return map;
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
        {open
          ? <ChevronUp size={16} className="text-gray-400" />
          : <ChevronDown size={16} className="text-gray-400" />}
      </button>
      {open && <div className="mt-4">{children}</div>}
    </div>
  );
}

// ─── Document preview helpers ─────────────────────────────────────────────────

function DocIcon({ mime, size = 20 }: { mime: string; size?: number }) {
  if (mime?.startsWith('image/')) return <Image size={size} className="text-blue-500" />;
  if (mime === 'application/pdf') return <FileText size={size} className="text-red-500" />;
  return <File size={size} className="text-gray-400" />;
}

function canPreview(mime: string): boolean {
  return mime?.startsWith('image/') || mime === 'application/pdf';
}

/**
 * Fetches the document via the authenticated apiClient and returns a
 * browser blob URL safe for use in <img> / <iframe> src attributes.
 *
 * Why: <img src="/api/..."> can't send an Authorization header, so the
 * endpoint would 401. Fetching with axios (which has the token interceptor)
 * then calling URL.createObjectURL() sidesteps that entirely.
 */
function useDocumentBlob(
  verificationId: string,
  documentId: string,
  enabled: boolean,
) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!enabled || !documentId) return;

    let objectUrl: string | null = null;
    setLoading(true);
    setError(false);

    apiClient
      .get(
        `/api/v1/transcripts/${verificationId}/documents/${documentId}/preview`,
        { responseType: 'blob' },
      )
      .then((res) => {
        // Explicitly preserve the content-type so the browser renders the
        // blob correctly (especially important for PDFs — without the right
        // MIME type the browser won't invoke its PDF viewer).
        const contentType: string =
          (res.headers['content-type'] as string | undefined)?.split(';')[0].trim() ||
          'application/octet-stream';
        const blob = new Blob([res.data as BlobPart], { type: contentType });
        objectUrl = URL.createObjectURL(blob);
        setBlobUrl(objectUrl);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });

    return () => {
      // Revoke the object URL when the component using it unmounts
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      setBlobUrl(null);
    };
  }, [verificationId, documentId, enabled]);

  return { blobUrl, loading, error };
}

// ─── Preview modal ────────────────────────────────────────────────────────────

function PreviewModal({
  doc,
  verificationId,
  onClose,
}: {
  doc: Record<string, unknown>;
  verificationId: string;
  onClose: () => void;
}) {
  const filename   = (doc.original_filename as string) || 'Document';
  const mime       = (doc.mime_type as string) || '';
  const documentId = (doc.document_id as string) || '';

  const isImage = mime.startsWith('image/');
  const isPdf   = mime === 'application/pdf';

  const [zoom, setZoom] = useState(100);
  const [rotation, setRotation] = useState(0);

  // Fetch via authenticated axios → blob URL (bypasses the auth header problem)
  const { blobUrl, loading, error } = useDocumentBlob(verificationId, documentId, true);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // Trigger browser download from the blob URL
  const handleDownload = () => {
    if (!blobUrl) return;
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename;
    a.click();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-black/85 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      {/* Toolbar */}
      <div className="flex items-center justify-between px-5 py-3 bg-gray-900 border-b border-white/10 shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <DocIcon mime={mime} size={16} />
          <span className="text-sm font-medium text-white truncate max-w-xs" title={filename}>
            {filename}
          </span>
          <span className="text-xs text-gray-500 shrink-0 hidden sm:block">{mime}</span>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {isImage && blobUrl && (
            <>
              <button
                onClick={() => setZoom((z) => Math.max(25, z - 25))}
                className="p-1.5 text-gray-400 hover:text-white hover:bg-white/10 rounded-lg transition-colors"
                title="Zoom out"
              >
                <ZoomOut size={15} />
              </button>
              <span className="text-xs text-gray-500 w-10 text-center select-none">{zoom}%</span>
              <button
                onClick={() => setZoom((z) => Math.min(300, z + 25))}
                className="p-1.5 text-gray-400 hover:text-white hover:bg-white/10 rounded-lg transition-colors"
                title="Zoom in"
              >
                <ZoomIn size={15} />
              </button>
              <button
                onClick={() => setRotation((r) => (r + 90) % 360)}
                className="p-1.5 text-gray-400 hover:text-white hover:bg-white/10 rounded-lg transition-colors"
                title="Rotate 90°"
              >
                <RotateCw size={15} />
              </button>
              <div className="w-px h-5 bg-white/20 mx-1" />
            </>
          )}

          <button
            onClick={handleDownload}
            disabled={!blobUrl}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-300 hover:text-white bg-white/10 hover:bg-white/20 rounded-lg transition-colors disabled:opacity-40"
          >
            <Download size={13} />
            Download
          </button>

          <button
            onClick={onClose}
            className="p-1.5 text-gray-400 hover:text-white hover:bg-white/10 rounded-lg transition-colors ml-1"
          >
            <X size={18} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto flex items-center justify-center p-4">
        {loading && (
          <div className="flex flex-col items-center gap-3 text-gray-400">
            <Spinner size={32} />
            <p className="text-sm">Loading document…</p>
          </div>
        )}

        {error && (
          <div className="text-center text-gray-400 space-y-2">
            <File size={48} className="mx-auto opacity-30" />
            <p className="text-sm">Failed to load document.</p>
            <p className="text-xs text-gray-500">The file may no longer exist on the server.</p>
          </div>
        )}

        {blobUrl && isImage && (
          <img
            src={blobUrl}
            alt={filename}
            style={{
              transform: `scale(${zoom / 100}) rotate(${rotation}deg)`,
              transformOrigin: 'center',
              transition: 'transform 0.2s ease',
              maxWidth: zoom <= 100 ? '100%' : 'none',
              maxHeight: zoom <= 100 ? 'calc(100vh - 120px)' : 'none',
            }}
            className="rounded shadow-2xl object-contain"
          />
        )}

        {blobUrl && isPdf && (
          // <object> handles PDF blob URLs more reliably than <iframe> across browsers.
          // <iframe> with a blob URL often shows a blank page because the browser's
          // built-in PDF viewer doesn't receive the correct MIME type via iframe.
          <object
            data={blobUrl}
            type="application/pdf"
            className="w-full max-w-5xl rounded shadow-2xl bg-white"
            style={{ height: 'calc(100vh - 120px)' }}
          >
            {/* Fallback for browsers that block inline PDF (e.g. Firefox strict mode) */}
            <div className="flex flex-col items-center justify-center h-full gap-4 text-gray-400 p-8">
              <FileText size={48} className="opacity-30" />
              <p className="text-sm text-center">
                Your browser cannot display the PDF inline.
              </p>
              <button
                onClick={handleDownload}
                className="flex items-center gap-2 px-4 py-2 bg-white/10 hover:bg-white/20 text-white text-sm rounded-lg transition-colors"
              >
                <Download size={15} /> Download PDF
              </button>
            </div>
          </object>
        )}
      </div>
    </div>
  );
}

// ─── Document card ────────────────────────────────────────────────────────────

function DocumentCard({
  doc,
  verificationId,
}: {
  doc: Record<string, unknown>;
  verificationId: string;
}) {
  const filename  = (doc.original_filename as string) || 'Unknown file';
  const mime      = (doc.mime_type as string) || '';
  const size      = doc.file_size_bytes as number;
  const docStatus = (doc.status as string) || '';
  const uploadedAt = doc.uploaded_at as string;
  const checksum  = (doc.checksum_sha256 as string) || '';
  const documentId = doc.document_id as string;

  const [showPreview, setShowPreview] = useState(false);

  const previewable = canPreview(mime);
  const isImage = mime.startsWith('image/');

  // Pre-fetch thumbnail for images via authenticated axios → blob URL
  const { blobUrl: thumbBlobUrl } = useDocumentBlob(verificationId, documentId, isImage);

  const statusColor =
    docStatus === 'processed'  ? 'bg-emerald-100 text-emerald-700' :
    docStatus === 'processing' ? 'bg-blue-100 text-blue-700'       :
    docStatus === 'failed'     ? 'bg-red-100 text-red-700'         :
                                  'bg-gray-100 text-gray-600';

  return (
    <>
      <div className="flex items-start gap-4 p-4 rounded-xl border border-gray-100 bg-gray-50 hover:bg-white transition-colors group">

        {/* Thumbnail / icon */}
        <div
          className={`w-14 h-14 rounded-lg border border-gray-200 bg-white flex items-center justify-center shrink-0 overflow-hidden shadow-sm ${previewable ? 'cursor-pointer' : ''}`}
          onClick={() => previewable && setShowPreview(true)}
        >
          {thumbBlobUrl ? (
            <img
              src={thumbBlobUrl}
              alt={filename}
              className="w-full h-full object-cover"
            />
          ) : (
            <DocIcon mime={mime} size={22} />
          )}
        </div>

        {/* Details */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-gray-800 truncate" title={filename}>
              {filename}
            </span>
            {docStatus && (
              <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full capitalize ${statusColor}`}>
                {docStatus}
              </span>
            )}
          </div>

          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1 text-xs text-gray-400">
            {mime && <span>{mime}</span>}
            {size > 0 && <span>{formatBytes(size)}</span>}
            {uploadedAt && <span>Uploaded {formatDateTime(uploadedAt)}</span>}
          </div>

          {checksum && (
            <p className="mt-1 text-[10px] font-mono text-gray-300 truncate" title={checksum}>
              SHA-256: {checksum}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          {previewable ? (
            <button
              onClick={() => setShowPreview(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 border border-blue-200 rounded-lg hover:bg-blue-100 transition-colors"
            >
              <Eye size={13} />
              Preview
            </button>
          ) : (
            <span className="text-xs text-gray-400 italic">No preview</span>
          )}
          <button
            onClick={() => {
              if (!thumbBlobUrl && !previewable) return;
              // For images we already have the blob; for PDFs open preview modal
              if (thumbBlobUrl) {
                const a = document.createElement('a');
                a.href = thumbBlobUrl;
                a.download = filename;
                a.click();
              } else {
                setShowPreview(true);
              }
            }}
            className="p-2 text-gray-400 hover:text-gray-600 transition-colors"
            title="Download"
          >
            <Download size={14} />
          </button>
        </div>
      </div>

      {showPreview && (
        <PreviewModal
          doc={doc}
          verificationId={verificationId}
          onClose={() => setShowPreview(false)}
        />
      )}
    </>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

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
    // Keep polling while the pipeline is running (e.g. after a retry)
    refetchInterval: (query) =>
      query.state.data?.status === 'in_progress' ? 3000 : false,
  });

  const { data: reviews } = useQuery({
    queryKey: ['reviews', id],
    queryFn: () => transcriptsApi.listReviews(id!),
    enabled: !!id,
  });

  // Fetch staff to resolve IDs → names
  const { data: staffList = [] } = useQuery({
    queryKey: ['staff-list'],
    queryFn: () => authApi.listStaff(),
    staleTime: 5 * 60 * 1000,
  });

  const staffMap = useMemo(() => buildStaffMap(staffList), [staffList]);

  // Derive display values
  const primaryDoc = transcript?.documents?.[0] as Record<string, unknown> | undefined;
  const transcriptFilename = (primaryDoc?.original_filename as string) || '—';
  const assignedStaffName = transcript?.assigned_staff_id
    ? (staffMap.get(transcript.assigned_staff_id) ?? transcript.assigned_staff_id)
    : null;

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

  const retryMutation = useMutation({
    mutationFn: () => transcriptsApi.retry(id!),
    onSuccess: () => {
      toast.success('Re-running verification pipeline…');
      // Invalidating causes the query to refetch; the refetchInterval above
      // will then keep polling until the status leaves in_progress.
      queryClient.invalidateQueries({ queryKey: ['transcript', id] });
    },
    onError: () => toast.error('Failed to retry — please try again.'),
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

  const flags: Array<Record<string, unknown>> =
    ((transcript.summary as Record<string, unknown>)?.flags as Record<string, unknown>[]) ?? [];

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
          <div className="flex items-center gap-3 flex-wrap">
            <StatusBadge status={transcript.status} />

            {/* Retry — shown when flagged so staff can re-run the pipeline */}
            {transcript.status === 'flagged' && (
              <button
                onClick={() => retryMutation.mutate()}
                disabled={retryMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-amber-700 bg-amber-50 border border-amber-200 rounded-lg hover:bg-amber-100 transition-colors disabled:opacity-60"
              >
                {retryMutation.isPending ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <RefreshCcw size={14} />
                )}
                {retryMutation.isPending ? 'Retrying…' : 'Retry Verification'}
              </button>
            )}

            {/* Re-analyzing indicator */}
            {transcript.status === 'in_progress' && (
              <span className="flex items-center gap-1.5 text-sm text-blue-600 font-medium">
                <Loader2 size={14} className="animate-spin" />
                Re-analyzing…
              </span>
            )}

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

          {/* Transcript filename instead of raw applicant_id */}
          <div>
            <p className="text-gray-400 text-xs mb-0.5">Transcript File</p>
            <div className="flex items-center gap-1.5">
              <FileText size={13} className="text-gray-400 shrink-0" />
              <p className="font-medium text-gray-800 truncate" title={transcriptFilename}>
                {transcriptFilename}
              </p>
            </div>
          </div>

          <div>
            <p className="text-gray-400 text-xs mb-0.5">Applicant Type</p>
            <p className="font-medium text-gray-800 capitalize">
              {transcript.applicant_type.replace('_', ' ')}
            </p>
          </div>

          {/* Assigned staff name instead of UUID */}
          <div>
            <p className="text-gray-400 text-xs mb-0.5">Assigned Reviewer</p>
            {assignedStaffName ? (
              <div className="flex items-center gap-1.5">
                <User size={13} className="text-gray-400 shrink-0" />
                <p className="font-medium text-gray-800">{assignedStaffName}</p>
              </div>
            ) : (
              <p className="text-gray-400 italic text-sm">Unassigned</p>
            )}
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
        ) : (() => {
          const s = transcript.summary as Record<string, unknown>;
          const riskLevel = s.risk_level as string;
          const fraudScore = typeof s.fraud_score === 'number' ? Math.round((s.fraud_score as number) * 100) : null;
          const recommendation = s.ai_recommendation as string;
          const extracted = s.extracted_data as Record<string, unknown> | undefined;
          const exNotes = typeof extracted?.extraction_notes === 'string' ? extracted.extraction_notes : '';

          const riskColor =
            riskLevel === 'LOW'    ? 'bg-emerald-50 text-emerald-700 border-emerald-200' :
            riskLevel === 'MEDIUM' ? 'bg-amber-50 text-amber-700 border-amber-200' :
            riskLevel === 'HIGH'   ? 'bg-red-50 text-red-700 border-red-200' :
                                     'bg-gray-50 text-gray-600 border-gray-200';

          return (
            <div className="space-y-4">
              {/* Risk + score */}
              {(riskLevel || fraudScore !== null) && (
                <div className="flex items-center gap-3 flex-wrap">
                  {riskLevel && (
                    <span className={`text-xs font-bold uppercase px-3 py-1 rounded-full border ${riskColor}`}>
                      {riskLevel} Risk
                    </span>
                  )}
                  {fraudScore !== null && (
                    <span className="text-xs text-gray-500">
                      Fraud score: <span className="font-semibold text-gray-700">{fraudScore}%</span>
                    </span>
                  )}
                </div>
              )}

              {/* AI recommendation */}
              {recommendation && (
                <div className="bg-blue-50 border border-blue-100 rounded-lg p-3">
                  <p className="text-xs font-semibold text-blue-700 uppercase mb-1">AI Recommendation</p>
                  <p className="text-sm text-blue-800">{recommendation}</p>
                </div>
              )}

              {/* Extracted data */}
              {extracted && (
                <div className="space-y-3">
                  <p className="text-xs font-semibold text-gray-500 uppercase">Extracted Data</p>

                  {/* Extraction error / notes */}
                  {exNotes ? (
                    <div className={`text-xs px-3 py-2 rounded-lg border ${
                      exNotes.startsWith('extraction error')
                        ? 'bg-red-50 border-red-200 text-red-700'
                        : 'bg-gray-50 border-gray-200 text-gray-600'
                    }`}>
                      <span className="font-semibold">Note: </span>{exNotes}
                    </div>
                  ) : null}

                  <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                    {([
                      { label: 'Institution', value: extracted.institution_name as string | undefined },
                      { label: 'Program', value: extracted.program_name as string | undefined },
                      { label: 'Degree', value: extracted.degree_awarded as string | undefined },
                      { label: 'Graduation Date', value: (extracted.graduation_date as string | undefined) || '—' },
                      { label: 'Graduation Confirmed', value: extracted.graduation_confirmed ? 'Yes' : 'No' },
                      { label: 'Total Credits', value: extracted.total_credits ? `${extracted.total_credits} hrs` : '—' },
                      { label: 'Nursing Credits', value: extracted.nursing_credits ? `${extracted.nursing_credits} hrs` : '—' },
                      { label: 'Accreditation', value: extracted.accreditation_type as string | undefined },
                    ] as { label: string; value: string | undefined }[]).map(({ label, value }) => value ? (
                      <div key={label}>
                        <dt className="text-xs text-gray-400">{label}</dt>
                        <dd className="font-medium text-gray-800">{value}</dd>
                      </div>
                    ) : null)}
                  </dl>

                </div>
              )}
            </div>
          );
        })()}
      </SectionBox>

      {/* Flags & Reviews */}
      {flags.length > 0 && (
        <SectionBox title={`AI Flags (${flags.length})`} icon={XCircle}>
          <div className="space-y-3">
            {flags.map((flag, idx) => {
              const flagId = flag.flag_id as string;
              const sev = flag.severity as string;
              const reviewed = reviews?.find((r) => r.flag_id === flagId);

              const borderBg =
                sev === 'critical' ? 'border-red-200 bg-red-50' :
                sev === 'warning'  ? 'border-amber-200 bg-amber-50' :
                                     'border-blue-200 bg-blue-50';
              const badgeColor =
                sev === 'critical' ? 'bg-red-100 text-red-700' :
                sev === 'warning'  ? 'bg-amber-100 text-amber-700' :
                                     'bg-blue-100 text-blue-700';

              return (
                <div key={flagId ?? idx} className={`border rounded-lg p-4 ${borderBg}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1 flex-wrap">
                        <span className={`text-xs font-bold uppercase px-1.5 py-0.5 rounded ${badgeColor}`}>
                          {sev}
                        </span>
                        <span className="text-xs text-gray-500 font-mono">{flag.rule_code as string}</span>
                        <span className="text-xs text-gray-400 capitalize">
                          {(flag.category as string)?.replace('_', ' ')}
                        </span>
                      </div>
                      <p className="text-sm font-semibold text-gray-800">{flag.rule_description as string}</p>
                      <p className="text-xs text-gray-500 mt-0.5 italic">Evidence: {flag.evidence as string}</p>
                      <p className="text-xs text-gray-600 mt-1">{flag.explanation as string}</p>
                    </div>

                    {reviewed ? (
                      <span className={`text-xs font-semibold px-2 py-1 rounded-full shrink-0 ${
                        reviewed.action === 'confirm'
                          ? 'bg-red-100 text-red-700'
                          : 'bg-purple-100 text-purple-700'
                      }`}>
                        {reviewed.action === 'confirm' ? '✓ Confirmed' : '↩ Overridden'}
                      </span>
                    ) : (
                      <div className="flex gap-2 flex-shrink-0">
                        <button
                          onClick={() => { setReviewFlagId(flagId); setReviewAction('confirm'); }}
                          className="text-xs bg-red-600 text-white px-2.5 py-1 rounded-lg hover:bg-red-700 flex items-center gap-1"
                        >
                          <CheckCircle size={12} /> Confirm
                        </button>
                        <button
                          onClick={() => { setReviewFlagId(flagId); setReviewAction('override'); }}
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
              const authorId = ann.staff_user_id as string;
              const authorName = staffMap.get(authorId) ?? authorId;
              return (
                <div key={i} className="bg-blue-50 border border-blue-100 rounded-lg p-3 text-sm">
                  <p className="text-gray-800">{String(ann.note ?? '')}</p>
                  <div className="flex items-center gap-2 text-xs text-gray-400 mt-1.5">
                    <User size={11} />
                    <span className="font-medium text-gray-500">{authorName}</span>
                    {ann.created_at != null && (
                      <span>· {formatDateTime(String(ann.created_at))}</span>
                    )}
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
            {reviews.map((r) => {
              const reviewerName = staffMap.get(r.staff_user_id) ?? r.staff_user_id;
              return (
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
                      Flag <span className="font-mono text-xs">{r.flag_id.slice(0, 8)}…</span>
                      {r.justification ? ` — ${r.justification}` : ''}
                    </p>
                    <p className="text-xs text-gray-400 mt-0.5 flex items-center gap-1">
                      <User size={10} />
                      {reviewerName} · {formatDateTime(r.created_at)}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        </SectionBox>
      )}

      {/* Documents — previewable cards */}
      {transcript.documents.length > 0 && (
        <SectionBox title={`Documents (${transcript.documents.length})`} icon={FileText} defaultOpen={true}>
          <div className="space-y-3">
            {transcript.documents.map((doc, idx) => (
              <DocumentCard
                key={idx}
                doc={doc as Record<string, unknown>}
                verificationId={transcript.verification_id}
              />
            ))}
          </div>
        </SectionBox>
      )}

      {/* Delete modal */}
      {showDeleteModal && (
        <ConfirmModal
          title="Delete Verification Record"
          message={`Permanently delete verification for "${transcriptFilename}"? This cannot be undone.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setShowDeleteModal(false)}
        />
      )}
    </div>
  );
}
