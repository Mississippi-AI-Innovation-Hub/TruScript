/**
 * NewVerification — interactive multi-file transcript upload + live progress + results
 *
 * Three phases:
 *   1. UPLOAD   — drag-and-drop zone, file list, applicant type selector
 *   2. PROGRESS — per-file pipeline step checklist with animations, live polling
 *   3. RESULTS  — per-file result card with risk badge, flag summary, actions
 */
import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Upload,
  FileText,
  Image,
  File,
  X,
  CheckCircle2,
  AlertTriangle,
  ShieldAlert,
  ShieldCheck,
  Loader2,
  Circle,
  ChevronDown,
  ChevronUp,
  ArrowRight,
  UserPlus,
  ThumbsUp,
  Clock,
  AlertCircle,
  RefreshCcw,
  Eye,
  Paperclip,
} from 'lucide-react';
import toast from 'react-hot-toast';
import apiClient from '../api/client';
import { transcriptsApi } from '../api/transcripts';
import type { TranscriptResponse, ApplicantType } from '../types';

// ─── Types ────────────────────────────────────────────────────────────────────

interface SelectedFile {
  id: string;          // local uuid for tracking
  file: File;
  preview?: string;    // object URL for images
}

interface UploadedItem {
  verification_id: string;
  filename: string;
  status: string;
  file_size_bytes: number;
  mime_type: string;
}

interface PipelineStep {
  id: string;
  label: string;
  description: string;
  /** Seconds from upload-submit until this step *starts* spinning */
  startAfterSecs: number;
  /** Seconds from upload-submit until this step *completes* (if still in_progress) */
  doneAfterSecs: number;
}

// ─── Pipeline steps definition ────────────────────────────────────────────────

const PIPELINE_STEPS: PipelineStep[] = [
  {
    id: 'upload',
    label: 'Document uploaded',
    description: 'Transcript securely uploaded to MSBN storage',
    startAfterSecs: 0,
    doneAfterSecs: 1,
  },
  {
    id: 'ocr',
    label: 'Text extraction (OCR)',
    description: 'AWS Textract reading all text, courses, and grades',
    startAfterSecs: 1,
    doneAfterSecs: 5,
  },
  {
    id: 'visual',
    label: 'Visual analysis',
    description: 'Checking for institutional seal, stamps, and formatting',
    startAfterSecs: 5,
    doneAfterSecs: 9,
  },
  {
    id: 'ai',
    label: 'AI fraud analysis',
    description: 'Claude AI evaluating against all 24 MSBN fraud detection rules',
    startAfterSecs: 9,
    doneAfterSecs: 16,
  },
  {
    id: 'report',
    label: 'Generating report',
    description: 'Building explainable audit trail and verification summary',
    startAfterSecs: 16,
    doneAfterSecs: 18,
  },
];

type StepState = 'idle' | 'active' | 'done' | 'error';

type PagePhase = 'upload' | 'processing' | 'results';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fileIcon(mime: string, _filename: string): React.ReactNode {
  if (mime.startsWith('image/')) return <Image size={16} className="text-blue-500" />;
  if (mime === 'application/pdf') return <FileText size={16} className="text-red-500" />;
  return <File size={16} className="text-gray-400" />;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getRiskLevel(summary: Record<string, unknown> | null): 'LOW' | 'MEDIUM' | 'HIGH' | null {
  if (!summary) return null;
  return (summary.risk_level as 'LOW' | 'MEDIUM' | 'HIGH') ?? null;
}

function getFraudScore(summary: Record<string, unknown> | null): number | null {
  if (!summary) return null;
  const v = summary.fraud_score;
  return typeof v === 'number' ? Math.round(v * 100) : null;
}

function getFlags(summary: Record<string, unknown> | null): Record<string, unknown>[] {
  if (!summary) return [];
  return (summary.flags as Record<string, unknown>[]) ?? [];
}

function getExtractedData(summary: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!summary) return null;
  return (summary.extracted_data as Record<string, unknown>) ?? null;
}

// ─── Risk badge ───────────────────────────────────────────────────────────────

function RiskBadge({ level }: { level: 'LOW' | 'MEDIUM' | 'HIGH' | null }) {
  if (!level) return null;
  const cfg = {
    LOW: {
      bg: 'bg-emerald-50',
      text: 'text-emerald-700',
      border: 'border-emerald-200',
      icon: <ShieldCheck size={14} className="text-emerald-600" />,
      label: 'LOW RISK',
    },
    MEDIUM: {
      bg: 'bg-amber-50',
      text: 'text-amber-700',
      border: 'border-amber-200',
      icon: <AlertTriangle size={14} className="text-amber-600" />,
      label: 'MEDIUM RISK',
    },
    HIGH: {
      bg: 'bg-red-50',
      text: 'text-red-700',
      border: 'border-red-200',
      icon: <ShieldAlert size={14} className="text-red-600" />,
      label: 'HIGH RISK',
    },
  }[level];

  return (
    <span
      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold border ${cfg.bg} ${cfg.text} ${cfg.border}`}
    >
      {cfg.icon}
      {cfg.label}
    </span>
  );
}

// ─── Step indicator ───────────────────────────────────────────────────────────

function StepRow({
  step,
  state,
  isLast,
}: {
  step: PipelineStep;
  state: StepState;
  isLast: boolean;
}) {
  const icon =
    state === 'done' ? (
      <CheckCircle2 size={18} className="text-emerald-500 flex-shrink-0" />
    ) : state === 'active' ? (
      <Loader2 size={18} className="text-brand-500 animate-spin flex-shrink-0" style={{ color: '#628ff2' }} />
    ) : state === 'error' ? (
      <AlertCircle size={18} className="text-red-500 flex-shrink-0" />
    ) : (
      <Circle size={18} className="text-gray-300 flex-shrink-0" />
    );

  const labelClass =
    state === 'done'
      ? 'text-gray-800 font-medium'
      : state === 'active'
      ? 'text-gray-900 font-semibold'
      : 'text-gray-400';

  const descClass = state === 'idle' ? 'text-gray-300' : 'text-gray-500';

  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        {icon}
        {!isLast && (
          <div
            className={`w-px flex-1 mt-1 ${
              state === 'done' ? 'bg-emerald-200' : 'bg-gray-100'
            }`}
            style={{ minHeight: 20 }}
          />
        )}
      </div>
      <div className="pb-4 min-w-0">
        <p className={`text-sm leading-tight ${labelClass}`}>{step.label}</p>
        <p className={`text-xs mt-0.5 ${descClass}`}>{step.description}</p>
      </div>
    </div>
  );
}

// ─── Per-file progress card ───────────────────────────────────────────────────

function ProgressCard({
  item,
  submitTime,
  transcriptData,
}: {
  item: UploadedItem;
  submitTime: number;
  transcriptData: TranscriptResponse | undefined;
}) {
  const [expanded, setExpanded] = useState(true);
  const [elapsed, setElapsed] = useState(0);

  // Tick elapsed seconds
  useEffect(() => {
    const iv = setInterval(() => {
      setElapsed(Math.floor((Date.now() - submitTime) / 1000));
    }, 500);
    return () => clearInterval(iv);
  }, [submitTime]);

  const isComplete =
    transcriptData?.status === 'flagged' ||
    transcriptData?.status === 'cleared';

  // Derive step states
  const stepStates: StepState[] = PIPELINE_STEPS.map((step, idx) => {
    if (isComplete) return 'done';
    if (elapsed >= step.doneAfterSecs && idx < PIPELINE_STEPS.length - 1) return 'done';
    if (elapsed >= step.startAfterSecs && elapsed < step.doneAfterSecs) return 'active';
    // Last step stays active until complete
    if (idx === PIPELINE_STEPS.length - 1 && elapsed >= step.startAfterSecs && !isComplete)
      return 'active';
    return 'idle';
  });

  const completedSteps = stepStates.filter((s) => s === 'done').length;
  const progress = isComplete ? 100 : Math.round((completedSteps / PIPELINE_STEPS.length) * 100);

  const filename = item.filename;
  const ext = filename.split('.').pop()?.toUpperCase() ?? '';

  return (
    <div className="border border-gray-200 rounded-xl overflow-hidden bg-white shadow-sm">
      {/* Header */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-gray-50 transition-colors text-left"
      >
        <div className="w-8 h-8 bg-gray-100 rounded-lg flex items-center justify-center flex-shrink-0">
          {fileIcon(item.mime_type, filename)}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-gray-800 truncate">{filename}</p>
          <p className="text-xs text-gray-400">{formatBytes(item.file_size_bytes)} · {ext}</p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {isComplete ? (
            <CheckCircle2 size={16} className="text-emerald-500" />
          ) : (
            <span className="text-xs text-gray-400">{progress}%</span>
          )}
          {expanded ? (
            <ChevronUp size={16} className="text-gray-400" />
          ) : (
            <ChevronDown size={16} className="text-gray-400" />
          )}
        </div>
      </button>

      {/* Progress bar */}
      <div className="h-1 bg-gray-100">
        <div
          className="h-1 transition-all duration-700"
          style={{
            width: `${progress}%`,
            background:
              isComplete && transcriptData?.status === 'flagged'
                ? 'linear-gradient(90deg, #f87171, #ef4444)'
                : 'linear-gradient(90deg, #7bc2fa, #628ff2)',
          }}
        />
      </div>

      {/* Steps */}
      {expanded && (
        <div className="px-5 pt-4 pb-2">
          {PIPELINE_STEPS.map((step, idx) => (
            <StepRow
              key={step.id}
              step={step}
              state={stepStates[idx]}
              isLast={idx === PIPELINE_STEPS.length - 1}
            />
          ))}

          {!isComplete && (
            <div className="flex items-center gap-2 mt-1 pb-3 text-xs text-gray-400">
              <Clock size={12} />
              <span>
                Elapsed: {elapsed}s · Estimated{' '}
                {Math.max(0, 18 - elapsed)}s remaining
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Per-file result card ─────────────────────────────────────────────────────

function ResultCard({
  item,
  transcriptData,
  onAssign,
}: {
  item: UploadedItem;
  transcriptData: TranscriptResponse | undefined;
  onAssign: (verificationId: string) => void;
}) {
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);

  const summary = transcriptData?.summary ?? null;
  const riskLevel = getRiskLevel(summary);
  const fraudScore = getFraudScore(summary);
  const flags = getFlags(summary);
  const extracted = getExtractedData(summary);
  const aiRec = (summary?.ai_recommendation as string) ?? '';

  const criticalCount = flags.filter((f) => f.severity === 'critical').length;
  const warningCount = flags.filter((f) => f.severity === 'warning').length;
  const infoCount = flags.filter((f) => f.severity === 'info').length;

  const isCleared = transcriptData?.status === 'cleared';

  return (
    <div
      className={`border-2 rounded-xl overflow-hidden bg-white shadow-sm ${
        isCleared
          ? 'border-emerald-200'
          : riskLevel === 'HIGH'
          ? 'border-red-200'
          : 'border-amber-200'
      }`}
    >
      {/* Header */}
      <div
        className={`px-5 py-4 ${
          isCleared
            ? 'bg-emerald-50'
            : riskLevel === 'HIGH'
            ? 'bg-red-50'
            : 'bg-amber-50'
        }`}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 bg-white rounded-lg shadow-sm flex items-center justify-center flex-shrink-0">
              {fileIcon(item.mime_type, item.filename)}
            </div>
            <div className="min-w-0">
              <p className="text-sm font-bold text-gray-900 truncate">{item.filename}</p>
              <div className="flex items-center gap-2 mt-0.5">
                <RiskBadge level={riskLevel} />
                {fraudScore !== null && (
                  <span className="text-xs text-gray-500">
                    Fraud score: <strong>{fraudScore}%</strong>
                  </span>
                )}
              </div>
            </div>
          </div>

          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0 mt-0.5"
          >
            {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
          </button>
        </div>

        {/* Flag counts */}
        {flags.length > 0 ? (
          <div className="flex items-center gap-3 mt-3">
            {criticalCount > 0 && (
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-red-700 bg-red-100 border border-red-200 rounded-full px-2 py-0.5">
                <AlertCircle size={11} />
                {criticalCount} Critical
              </span>
            )}
            {warningCount > 0 && (
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-amber-700 bg-amber-100 border border-amber-200 rounded-full px-2 py-0.5">
                <AlertTriangle size={11} />
                {warningCount} Warning
              </span>
            )}
            {infoCount > 0 && (
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-blue-700 bg-blue-100 border border-blue-200 rounded-full px-2 py-0.5">
                <AlertCircle size={11} />
                {infoCount} Info
              </span>
            )}
          </div>
        ) : (
          <p className="mt-2 text-xs text-emerald-600 font-medium">
            ✓ No fraud indicators detected
          </p>
        )}
      </div>

      {/* Expandable details */}
      {expanded && (
        <div className="px-5 py-4 border-t border-gray-100 space-y-4">

          {/* AI Recommendation */}
          {aiRec && (
            <div className="bg-gray-50 rounded-lg p-3">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
                AI Recommendation
              </p>
              <p className="text-sm text-gray-700 leading-relaxed">{aiRec}</p>
            </div>
          )}

          {/* Extracted data */}
          {extracted && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Extracted Information
              </p>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
                {[
                  { label: 'Institution', value: extracted.institution_name as string },
                  { label: 'Program', value: extracted.program_name as string },
                  { label: 'Degree', value: extracted.degree_awarded as string },
                  { label: 'Graduation', value: extracted.graduation_date as string || 'Not found' },
                  { label: 'Accreditation', value: extracted.accreditation_type as string },
                  {
                    label: 'Nursing Credits',
                    value: extracted.nursing_credits
                      ? `${extracted.nursing_credits} hrs`
                      : '—',
                  },
                ].map(({ label, value }) => (
                  <div key={label}>
                    <dt className="text-xs text-gray-400">{label}</dt>
                    <dd className="text-sm text-gray-800 font-medium">{String(value || '—')}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          {/* Flags */}
          {flags.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Detected Issues ({flags.length})
              </p>
              <div className="space-y-2">
                {flags.map((flag, idx) => {
                  const sev = flag.severity as string;
                  const borderColor =
                    sev === 'critical'
                      ? 'border-red-200 bg-red-50'
                      : sev === 'warning'
                      ? 'border-amber-200 bg-amber-50'
                      : 'border-blue-200 bg-blue-50';
                  const badgeColor =
                    sev === 'critical'
                      ? 'text-red-700 bg-red-100'
                      : sev === 'warning'
                      ? 'text-amber-700 bg-amber-100'
                      : 'text-blue-700 bg-blue-100';

                  return (
                    <div
                      key={idx}
                      className={`rounded-lg border p-3 ${borderColor}`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <span
                            className={`text-xs font-bold uppercase px-1.5 py-0.5 rounded ${badgeColor}`}
                          >
                            {sev}
                          </span>
                          <span className="ml-2 text-xs text-gray-500 font-mono">
                            {flag.rule_code as string}
                          </span>
                        </div>
                        <span className="text-xs text-gray-400 shrink-0">
                          {String(flag.category as string).replace('_', ' ')}
                        </span>
                      </div>
                      <p className="mt-1 text-sm font-semibold text-gray-800">
                        {flag.rule_description as string}
                      </p>
                      <p className="mt-0.5 text-xs text-gray-600 italic">
                        Evidence: {flag.evidence as string}
                      </p>
                      <p className="mt-1 text-xs text-gray-600">
                        {flag.explanation as string}
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Action bar */}
      <div className="flex items-center gap-2 px-5 py-3 border-t border-gray-100 bg-gray-50/50">
        <button
          onClick={() => navigate(`/transcripts/${item.verification_id}`)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
        >
          <Eye size={14} />
          Full Details
        </button>

        <button
          onClick={() => onAssign(item.verification_id)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
        >
          <UserPlus size={14} />
          Assign Reviewer
        </button>

        {isCleared && (
          <button
            onClick={() => {
              toast.success(`Transcript approved`);
              navigate(`/transcripts/${item.verification_id}`);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg hover:bg-emerald-100 transition-colors ml-auto"
          >
            <ThumbsUp size={14} />
            Approve
          </button>
        )}

        {!isCleared && criticalCount > 0 && (
          <span className="ml-auto text-xs text-red-600 font-medium flex items-center gap-1">
            <AlertCircle size={12} />
            Requires manual review
          </span>
        )}
      </div>
    </div>
  );
}

// ─── Assign modal ─────────────────────────────────────────────────────────────

interface StaffUser {
  id: string;
  username: string;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  enabled: boolean;
  roles: string[];
}

function staffInitials(s: StaffUser): string {
  const f = s.first_name?.[0] ?? '';
  const l = s.last_name?.[0] ?? '';
  return (f + l).toUpperCase() || s.username.slice(0, 2).toUpperCase();
}

function staffDisplayName(s: StaffUser): string {
  if (s.first_name && s.last_name) return `${s.first_name} ${s.last_name}`;
  return s.username;
}

function staffRoleLabel(s: StaffUser): string {
  if (s.roles.includes('msbn-admin')) return 'Admin';
  return 'Staff';
}

// Pastel avatar colours cycling by index
const AVATAR_COLORS = [
  'bg-blue-100 text-blue-700',
  'bg-violet-100 text-violet-700',
  'bg-emerald-100 text-emerald-700',
  'bg-amber-100 text-amber-700',
  'bg-rose-100 text-rose-700',
  'bg-cyan-100 text-cyan-700',
];

function AssignModal({
  verificationId,
  onClose,
}: {
  verificationId: string;
  onClose: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string>('');
  const [search, setSearch] = useState('');
  const [saving, setSaving] = useState(false);
  const [staff, setStaff] = useState<StaffUser[]>([]);
  const [loadingStaff, setLoadingStaff] = useState(true);
  const [staffError, setStaffError] = useState<string | null>(null);

  // Fetch staff list when modal opens
  useEffect(() => {
    let cancelled = false;
    setLoadingStaff(true);
    setStaffError(null);

    apiClient
      .get<StaffUser[]>('/api/v1/auth/staff')
      .then((res) => {
        if (!cancelled) {
          setStaff(res.data.filter((u) => u.enabled));
          setLoadingStaff(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setStaffError('Could not load staff list. Please try again.');
          setLoadingStaff(false);
        }
      });

    return () => { cancelled = true; };
  }, []);

  const filtered = staff.filter((s) => {
    const q = search.toLowerCase();
    return (
      staffDisplayName(s).toLowerCase().includes(q) ||
      s.username.toLowerCase().includes(q) ||
      (s.email ?? '').toLowerCase().includes(q)
    );
  });

  const handleSave = async () => {
    if (!selectedId) {
      toast.error('Please select a staff member to assign');
      return;
    }
    setSaving(true);
    try {
      await transcriptsApi.update(verificationId, { assigned_staff_id: selectedId });
      const assignee = staff.find((s) => s.id === selectedId);
      toast.success(`Assigned to ${assignee ? staffDisplayName(assignee) : 'reviewer'}`);
      onClose();
    } catch {
      toast.error('Failed to assign reviewer');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md flex flex-col" style={{ maxHeight: '90vh' }}>

        {/* Header */}
        <div className="flex items-center gap-3 px-6 pt-6 pb-4 border-b border-gray-100">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'linear-gradient(135deg,#7bc2fa,#628ff2)' }}
          >
            <UserPlus size={20} className="text-white" />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="font-bold text-gray-900">Assign Reviewer</h3>
            <p className="text-xs text-gray-400 truncate">
              Verification {verificationId.slice(0, 8)}…
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors p-1"
          >
            <X size={18} />
          </button>
        </div>

        {/* Search */}
        <div className="px-6 py-3 border-b border-gray-100">
          <div className="relative">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name, username, or email…"
              className="w-full border border-gray-200 rounded-lg pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 bg-gray-50"
              autoFocus
            />
            <svg
              className="absolute left-3 top-2.5 text-gray-400"
              width="14" height="14" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round"
            >
              <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
            </svg>
          </div>
        </div>

        {/* Staff list */}
        <div className="flex-1 overflow-y-auto px-4 py-2 min-h-0">
          {loadingStaff ? (
            <div className="flex flex-col items-center justify-center py-10 gap-3">
              <Loader2 size={24} className="animate-spin text-blue-400" />
              <p className="text-sm text-gray-400">Loading staff members…</p>
            </div>
          ) : staffError ? (
            <div className="flex flex-col items-center justify-center py-10 gap-2 text-center">
              <AlertCircle size={22} className="text-red-400" />
              <p className="text-sm text-red-600">{staffError}</p>
              <button
                onClick={() => {
                  setStaffError(null);
                  setLoadingStaff(true);
                  apiClient.get<StaffUser[]>('/api/v1/auth/staff')
                    .then((r) => { setStaff(r.data.filter((u) => u.enabled)); setLoadingStaff(false); })
                    .catch(() => { setStaffError('Could not load staff list.'); setLoadingStaff(false); });
                }}
                className="text-xs text-blue-600 underline"
              >
                Retry
              </button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 gap-2 text-center">
              <p className="text-sm text-gray-400">
                {search ? 'No staff members match your search.' : 'No staff members found.'}
              </p>
            </div>
          ) : (
            <ul className="space-y-1 py-1">
              {filtered.map((s, idx) => {
                const isSelected = selectedId === s.id;
                const avatarCls = AVATAR_COLORS[idx % AVATAR_COLORS.length];
                const isAdmin = s.roles.includes('msbn-admin');
                return (
                  <li key={s.id}>
                    <button
                      onClick={() => setSelectedId(s.id)}
                      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-left transition-all ${
                        isSelected
                          ? 'bg-blue-50 ring-2 ring-blue-300'
                          : 'hover:bg-gray-50'
                      }`}
                    >
                      {/* Avatar */}
                      <div
                        className={`w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold shrink-0 ${avatarCls}`}
                      >
                        {staffInitials(s)}
                      </div>

                      {/* Name & info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="text-sm font-semibold text-gray-800 truncate">
                            {staffDisplayName(s)}
                          </span>
                          <span
                            className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full shrink-0 ${
                              isAdmin
                                ? 'bg-violet-100 text-violet-700'
                                : 'bg-blue-100 text-blue-700'
                            }`}
                          >
                            {staffRoleLabel(s)}
                          </span>
                        </div>
                        <p className="text-xs text-gray-400 truncate">
                          @{s.username}{s.email ? ` · ${s.email}` : ''}
                        </p>
                      </div>

                      {/* Selection indicator */}
                      <div
                        className={`w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-all ${
                          isSelected
                            ? 'border-blue-500 bg-blue-500'
                            : 'border-gray-300'
                        }`}
                      >
                        {isSelected && (
                          <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
                            <path d="M2 6l3 3 5-5" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        )}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-100 flex items-center gap-3">
          {selectedId && (() => {
            const s = staff.find((x) => x.id === selectedId);
            return s ? (
              <p className="flex-1 text-xs text-gray-500 truncate">
                Assigning to <span className="font-semibold text-gray-700">{staffDisplayName(s)}</span>
              </p>
            ) : null;
          })()}
          {!selectedId && <div className="flex-1" />}

          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !selectedId || loadingStaff}
            className="px-5 py-2 text-sm font-medium text-white rounded-lg transition-colors disabled:opacity-50"
            style={{ background: 'linear-gradient(135deg,#7bc2fa,#628ff2)' }}
          >
            {saving ? (
              <span className="flex items-center gap-2">
                <Loader2 size={14} className="animate-spin" /> Assigning…
              </span>
            ) : 'Assign'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Polling hook ─────────────────────────────────────────────────────────────

function useTranscriptPoll(
  verificationIds: string[],
  enabled: boolean,
): Map<string, TranscriptResponse> {
  const [data, setData] = useState<Map<string, TranscriptResponse>>(new Map());

  useEffect(() => {
    if (!enabled || verificationIds.length === 0) return;

    let cancelled = false;

    const poll = async () => {
      const pending = verificationIds.filter((id) => {
        const d = data.get(id);
        return !d || (d.status !== 'flagged' && d.status !== 'cleared');
      });

      if (pending.length === 0) return;

      await Promise.all(
        pending.map(async (id) => {
          try {
            const result = await transcriptsApi.get(id);
            if (!cancelled) {
              setData((prev) => new Map(prev).set(id, result));
            }
          } catch {
            // ignore transient errors during polling
          }
        }),
      );
    };

    poll();
    const iv = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [enabled, verificationIds.join(',')]);

  return data;
}

// ─── Main page ────────────────────────────────────────────────────────────────

export function NewVerification() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<PagePhase>('upload');
  const [selectedFiles, setSelectedFiles] = useState<SelectedFile[]>([]);
  const [applicantType, setApplicantType] = useState<ApplicantType>('first_time');
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadedItems, setUploadedItems] = useState<UploadedItem[]>([]);
  const [submitTime, setSubmitTime] = useState(0);
  const [assignModalId, setAssignModalId] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);

  // Poll all uploaded verifications until complete
  const pollingActive = phase === 'processing' || phase === 'results';
  const verificationIds = uploadedItems.map((i) => i.verification_id);
  const transcriptDataMap = useTranscriptPoll(verificationIds, pollingActive);

  // Auto-advance to results when all complete
  useEffect(() => {
    if (phase !== 'processing') return;
    if (uploadedItems.length === 0) return;

    const allDone = uploadedItems.every((item) => {
      const d = transcriptDataMap.get(item.verification_id);
      return d?.status === 'flagged' || d?.status === 'cleared';
    });

    if (allDone) {
      // Give a short pause so last step animates to done
      setTimeout(() => setPhase('results'), 800);
    }
  }, [transcriptDataMap, uploadedItems, phase]);

  // ── File selection ──────────────────────────────────────────────────────────

  const addFiles = useCallback((incoming: FileList | File[]) => {
    const arr = Array.from(incoming);
    const valid = arr.filter((f) => {
      const ext = f.name.split('.').pop()?.toLowerCase() ?? '';
      return ['jpg', 'jpeg', 'png', 'tiff', 'pdf', 'docx', 'doc'].includes(ext);
    });
    if (valid.length < arr.length) {
      toast.error('Some files were skipped — only JPEG, PNG, PDF, and DOCX are allowed.');
    }
    setSelectedFiles((prev) => {
      const existingNames = new Set(prev.map((f) => f.file.name));
      const news = valid
        .filter((f) => !existingNames.has(f.name))
        .map((f) => ({
          id: crypto.randomUUID(),
          file: f,
          preview: f.type.startsWith('image/') ? URL.createObjectURL(f) : undefined,
        }));
      return [...prev, ...news];
    });
  }, []);

  const removeFile = (id: string) => {
    setSelectedFiles((prev) => {
      const f = prev.find((x) => x.id === id);
      if (f?.preview) URL.revokeObjectURL(f.preview);
      return prev.filter((x) => x.id !== id);
    });
  };

  // ── Drag & drop ─────────────────────────────────────────────────────────────

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(true);
  };

  const onDragLeave = () => setDragging(false);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files) addFiles(e.dataTransfer.files);
  };

  // ── Submit ──────────────────────────────────────────────────────────────────

  const handleSubmit = async () => {
    if (selectedFiles.length === 0) {
      toast.error('Please select at least one transcript file.');
      return;
    }

    setUploading(true);
    setUploadProgress(0);

    try {
      const formData = new FormData();
      selectedFiles.forEach((sf) => formData.append('files', sf.file));
      formData.append('applicant_type', applicantType);

      const response = await apiClient.post<UploadedItem[]>(
        '/api/v1/transcripts/upload',
        formData,
        {
          headers: { 'Content-Type': 'multipart/form-data' },
          onUploadProgress: (e) => {
            if (e.total) setUploadProgress(Math.round((e.loaded / e.total) * 100));
          },
        },
      );

      setUploadedItems(response.data);
      setSubmitTime(Date.now());
      setPhase('processing');
    } catch (err: any) {
      const msg =
        err?.response?.data?.detail ?? 'Upload failed. Please try again.';
      toast.error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    } finally {
      setUploading(false);
    }
  };

  // ── Reset ───────────────────────────────────────────────────────────────────

  const handleReset = () => {
    selectedFiles.forEach((f) => {
      if (f.preview) URL.revokeObjectURL(f.preview);
    });
    setSelectedFiles([]);
    setUploadedItems([]);
    setPhase('upload');
    setUploadProgress(0);
  };

  // ─────────────────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-full bg-gray-50">
      {/* ── Page header ──────────────────────────────────────────────────── */}
      <div className="bg-white border-b border-gray-200 px-6 py-5">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">New Verification</h1>
            <p className="text-sm text-gray-500 mt-0.5">
              Upload one or more transcripts — AI analysis runs automatically
            </p>
          </div>

          {/* Phase indicator */}
          <div className="hidden sm:flex items-center gap-1">
            {(['upload', 'processing', 'results'] as PagePhase[]).map((p, idx) => {
              const labels = ['Upload', 'Analysis', 'Results'];
              const isActive = phase === p;
              const isDone =
                (p === 'upload' && (phase === 'processing' || phase === 'results')) ||
                (p === 'processing' && phase === 'results');

              return (
                <React.Fragment key={p}>
                  {idx > 0 && (
                    <div
                      className={`w-6 h-px ${isDone ? 'bg-blue-400' : 'bg-gray-200'}`}
                    />
                  )}
                  <div
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold transition-colors ${
                      isActive
                        ? 'text-white'
                        : isDone
                        ? 'text-blue-600 bg-blue-50'
                        : 'text-gray-400 bg-gray-100'
                    }`}
                    style={isActive ? { background: 'linear-gradient(0deg,#7bc2fa,#628ff2)' } : {}}
                  >
                    {isDone ? (
                      <CheckCircle2 size={11} />
                    ) : (
                      <span>{idx + 1}</span>
                    )}
                    {labels[idx]}
                  </div>
                </React.Fragment>
              );
            })}
          </div>
        </div>
      </div>

      <div className="max-w-3xl mx-auto px-4 py-8 space-y-6">

        {/* ══════════════════════════════════════════════════════════════════
            PHASE 1: UPLOAD
        ══════════════════════════════════════════════════════════════════ */}
        {phase === 'upload' && (
          <>
            {/* Info banner */}
            <div className="flex gap-3 p-4 bg-blue-50 border border-blue-100 rounded-xl">
              <ShieldCheck size={20} className="text-blue-500 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-blue-700">
                <p className="font-semibold mb-0.5">AI-Powered Fraud Detection</p>
                <p className="text-xs leading-relaxed text-blue-600">
                  Each transcript is analyzed against 24 MSBN fraud-detection rules using
                  AWS Textract (OCR), Rekognition (visual analysis), and Claude AI (reasoning).
                  Results include explainable flags with supporting evidence — no black-box decisions.
                </p>
              </div>
            </div>

            {/* Drop zone */}
            <div
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
              className={`relative flex flex-col items-center justify-center gap-3 p-10 rounded-2xl border-2 border-dashed cursor-pointer transition-all ${
                dragging
                  ? 'border-blue-400 bg-blue-50 scale-[1.01]'
                  : 'border-gray-300 bg-white hover:border-blue-300 hover:bg-gray-50'
              }`}
            >
              <div
                className="w-14 h-14 rounded-2xl flex items-center justify-center"
                style={{ background: 'linear-gradient(0deg,#7bc2fa,#628ff2)' }}
              >
                <Upload size={26} className="text-white" />
              </div>
              <div className="text-center">
                <p className="font-semibold text-gray-800">
                  {dragging ? 'Drop files here' : 'Drag & drop transcripts here'}
                </p>
                <p className="text-sm text-gray-400 mt-0.5">
                  or <span className="text-blue-500 font-medium">browse files</span>
                </p>
              </div>
              <div className="flex items-center gap-2 flex-wrap justify-center">
                {['PDF', 'JPEG', 'PNG', 'DOCX'].map((ext) => (
                  <span
                    key={ext}
                    className="px-2 py-0.5 bg-gray-100 text-gray-500 text-xs rounded font-mono"
                  >
                    .{ext.toLowerCase()}
                  </span>
                ))}
                <span className="text-xs text-gray-400">· Max 25 MB per file</span>
              </div>
              <input
                ref={inputRef}
                type="file"
                multiple
                accept=".pdf,.jpg,.jpeg,.png,.tiff,.docx,.doc"
                className="hidden"
                onChange={(e) => e.target.files && addFiles(e.target.files)}
              />
            </div>

            {/* Selected files list */}
            {selectedFiles.length > 0 && (
              <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 bg-gray-50">
                  <p className="text-sm font-semibold text-gray-700">
                    <Paperclip size={14} className="inline mr-1.5 text-gray-400" />
                    {selectedFiles.length} file{selectedFiles.length !== 1 ? 's' : ''} selected
                  </p>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      selectedFiles.forEach((f) => f.preview && URL.revokeObjectURL(f.preview));
                      setSelectedFiles([]);
                    }}
                    className="text-xs text-gray-400 hover:text-red-500 transition-colors"
                  >
                    Remove all
                  </button>
                </div>
                <ul className="divide-y divide-gray-100">
                  {selectedFiles.map((sf) => (
                    <li key={sf.id} className="flex items-center gap-3 px-4 py-3">
                      {sf.preview ? (
                        <img
                          src={sf.preview}
                          alt={sf.file.name}
                          className="w-9 h-9 object-cover rounded-lg flex-shrink-0"
                        />
                      ) : (
                        <div className="w-9 h-9 bg-gray-100 rounded-lg flex items-center justify-center flex-shrink-0">
                          {fileIcon(sf.file.type, sf.file.name)}
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-800 truncate">
                          {sf.file.name}
                        </p>
                        <p className="text-xs text-gray-400">
                          {formatBytes(sf.file.size)}
                        </p>
                      </div>
                      <button
                        onClick={() => removeFile(sf.id)}
                        className="w-7 h-7 flex items-center justify-center rounded-full text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                      >
                        <X size={14} />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Applicant type */}
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <p className="text-sm font-semibold text-gray-700 mb-3">Applicant Type</p>
              <div className="grid grid-cols-2 gap-3">
                {(
                  [
                    {
                      value: 'first_time',
                      label: 'First-Time Applicant',
                      desc: 'Initial nursing licensure in Mississippi',
                    },
                    {
                      value: 'endorsement',
                      label: 'Endorsement',
                      desc: 'Currently licensed in another state',
                    },
                  ] as { value: ApplicantType; label: string; desc: string }[]
                ).map(({ value, label, desc }) => (
                  <button
                    key={value}
                    onClick={() => setApplicantType(value)}
                    className={`flex flex-col items-start gap-1 p-4 rounded-xl border-2 text-left transition-all ${
                      applicantType === value
                        ? 'border-blue-400 bg-blue-50'
                        : 'border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <div
                        className={`w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center ${
                          applicantType === value
                            ? 'border-blue-500'
                            : 'border-gray-300'
                        }`}
                      >
                        {applicantType === value && (
                          <div className="w-1.5 h-1.5 bg-blue-500 rounded-full" />
                        )}
                      </div>
                      <span className="text-sm font-semibold text-gray-800">{label}</span>
                    </div>
                    <p className="text-xs text-gray-500 pl-5">{desc}</p>
                  </button>
                ))}
              </div>
            </div>

            {/* Submit */}
            <button
              onClick={handleSubmit}
              disabled={selectedFiles.length === 0 || uploading}
              className="w-full flex items-center justify-center gap-2 py-3.5 rounded-xl text-base font-bold text-white transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ background: 'linear-gradient(0deg,#7bc2fa,#628ff2)' }}
            >
              {uploading ? (
                <>
                  <Loader2 size={20} className="animate-spin" />
                  Uploading… {uploadProgress}%
                </>
              ) : (
                <>
                  Start Verification
                  <ArrowRight size={18} />
                </>
              )}
            </button>
          </>
        )}

        {/* ══════════════════════════════════════════════════════════════════
            PHASE 2: PROCESSING
        ══════════════════════════════════════════════════════════════════ */}
        {phase === 'processing' && (
          <>
            <div className="flex items-center gap-3">
              <Loader2 size={22} className="animate-spin" style={{ color: '#628ff2' }} />
              <div>
                <h2 className="text-lg font-bold text-gray-900">
                  Analyzing {uploadedItems.length} Transcript{uploadedItems.length !== 1 ? 's' : ''}…
                </h2>
                <p className="text-sm text-gray-500">
                  The AI pipeline is running. This typically takes 15–20 seconds per transcript.
                </p>
              </div>
            </div>

            <div className="space-y-3">
              {uploadedItems.map((item) => (
                <ProgressCard
                  key={item.verification_id}
                  item={item}
                  submitTime={submitTime}
                  transcriptData={transcriptDataMap.get(item.verification_id)}
                />
              ))}
            </div>

            <p className="text-center text-sm text-gray-400">
              You can safely navigate away — verification continues in the background
            </p>
          </>
        )}

        {/* ══════════════════════════════════════════════════════════════════
            PHASE 3: RESULTS
        ══════════════════════════════════════════════════════════════════ */}
        {phase === 'results' && (
          <>
            {/* Summary banner */}
            {(() => {
              const all = uploadedItems.map((i) => transcriptDataMap.get(i.verification_id));
              const cleared = all.filter((d) => d?.status === 'cleared').length;
              const flagged = all.filter((d) => d?.status === 'flagged').length;
              return (
                <div
                  className={`flex items-start gap-4 p-4 rounded-xl border ${
                    flagged > 0
                      ? 'bg-amber-50 border-amber-200'
                      : 'bg-emerald-50 border-emerald-200'
                  }`}
                >
                  {flagged > 0 ? (
                    <AlertTriangle size={22} className="text-amber-500 flex-shrink-0 mt-0.5" />
                  ) : (
                    <CheckCircle2 size={22} className="text-emerald-500 flex-shrink-0 mt-0.5" />
                  )}
                  <div>
                    <p className="font-bold text-gray-900">
                      Verification Complete
                    </p>
                    <p className="text-sm text-gray-600 mt-0.5">
                      {cleared > 0 && (
                        <span className="text-emerald-700 font-medium">
                          {cleared} transcript{cleared !== 1 ? 's' : ''} cleared
                        </span>
                      )}
                      {cleared > 0 && flagged > 0 && <span className="text-gray-400"> · </span>}
                      {flagged > 0 && (
                        <span className="text-amber-700 font-medium">
                          {flagged} transcript{flagged !== 1 ? 's' : ''} flagged for review
                        </span>
                      )}
                    </p>
                  </div>
                  <button
                    onClick={handleReset}
                    className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors flex-shrink-0"
                  >
                    <RefreshCcw size={14} />
                    New Upload
                  </button>
                </div>
              );
            })()}

            {/* Result cards */}
            <div className="space-y-4">
              {uploadedItems.map((item) => (
                <ResultCard
                  key={item.verification_id}
                  item={item}
                  transcriptData={transcriptDataMap.get(item.verification_id)}
                  onAssign={setAssignModalId}
                />
              ))}
            </div>

            <div className="flex justify-center">
              <button
                onClick={() => navigate('/transcripts')}
                className="flex items-center gap-2 px-5 py-2.5 text-sm font-semibold text-white rounded-xl transition-colors"
                style={{ background: 'linear-gradient(0deg,#7bc2fa,#628ff2)' }}
              >
                View All Verifications
                <ArrowRight size={16} />
              </button>
            </div>
          </>
        )}
      </div>

      {/* Assign modal */}
      {assignModalId && (
        <AssignModal
          verificationId={assignModalId}
          onClose={() => setAssignModalId(null)}
        />
      )}
    </div>
  );
}
