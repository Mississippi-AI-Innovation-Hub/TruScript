import type { VerificationStatus } from '../types';

const config: Record<VerificationStatus, { label: string; className: string }> = {
  pending:     { label: 'Pending',     className: 'bg-yellow-100 text-yellow-800 border-yellow-200' },
  in_progress: { label: 'In Progress', className: 'bg-blue-100 text-blue-800 border-blue-200' },
  flagged:     { label: 'Flagged',     className: 'bg-red-100 text-red-800 border-red-200' },
  cleared:     { label: 'Cleared',     className: 'bg-green-100 text-green-800 border-green-200' },
  overridden:  { label: 'Overridden',  className: 'bg-purple-100 text-purple-800 border-purple-200' },
};

export function StatusBadge({ status }: { status: string }) {
  const s = status as VerificationStatus;
  const { label, className } = config[s] ?? { label: status, className: 'bg-gray-100 text-gray-700 border-gray-200' };
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${className}`}>
      {label}
    </span>
  );
}
