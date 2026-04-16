import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { FileText, CheckCircle, AlertTriangle, Clock, ArrowRight, TrendingUp, File, Image } from 'lucide-react';
import { transcriptsApi } from '../api';
import { useAuth } from '../context/AuthContext';
import { StatusBadge } from '../components/StatusBadge';
import { Spinner } from '../components/Spinner';
import type { TranscriptResponse, VerificationStatus } from '../types';

function getFilename(t: TranscriptResponse): string {
  const doc = t.documents?.[0] as Record<string, unknown> | undefined;
  return (doc?.original_filename as string) || t.applicant_id;
}

function FileIcon({ mime }: { mime: string }) {
  if (mime?.startsWith('image/')) return <Image size={14} className="text-blue-400 shrink-0" />;
  if (mime === 'application/pdf') return <FileText size={14} className="text-red-400 shrink-0" />;
  return <File size={14} className="text-gray-400 shrink-0" />;
}

const statusOrder: VerificationStatus[] = ['pending', 'in_progress', 'flagged', 'cleared', 'overridden'];

function StatCard({
  label,
  value,
  icon: Icon,
  color,
}: {
  label: string;
  value: number;
  icon: React.ElementType;
  color: string;
}) {
  return (
    <div className="card flex items-center gap-4">
      <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${color}`}>
        <Icon size={22} className="text-white" />
      </div>
      <div>
        <p className="text-2xl font-bold text-gray-900">{value}</p>
        <p className="text-sm text-gray-500">{label}</p>
      </div>
    </div>
  );
}

export function Dashboard() {
  const { user } = useAuth();

  const { data, isLoading } = useQuery({
    queryKey: ['transcripts', 'all'],
    queryFn: () => transcriptsApi.list(0, 100),
  });

  const counts = (statusOrder).reduce<Record<string, number>>((acc, s) => {
    acc[s] = data?.items.filter((t) => t.status === s).length ?? 0;
    return acc;
  }, {});

  const recent = data?.items.slice(0, 5) ?? [];

  return (
    <div className="p-8">
      {/* Greeting */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-brand-900">
          Welcome back, {user?.name?.split(' ')[0] ?? user?.username} 👋
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          Mississippi State Board of Nursing — Transcript Verification Dashboard
        </p>
      </div>

      {/* Stats */}
      {isLoading ? (
        <div className="flex justify-center py-10"><Spinner size={32} /></div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
            <StatCard
              label="Total Verifications"
              value={data?.total ?? 0}
              icon={FileText}
              color="bg-brand-500"
            />
            <StatCard
              label="Pending Review"
              value={(counts.pending ?? 0) + (counts.in_progress ?? 0)}
              icon={Clock}
              color="bg-yellow-400"
            />
            <StatCard
              label="Flagged"
              value={counts.flagged ?? 0}
              icon={AlertTriangle}
              color="bg-red-500"
            />
            <StatCard
              label="Cleared"
              value={(counts.cleared ?? 0) + (counts.overridden ?? 0)}
              icon={CheckCircle}
              color="bg-green-500"
            />
          </div>

          {/* Status breakdown */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Breakdown bar */}
            <div className="card">
              <div className="flex items-center gap-2 mb-4">
                <TrendingUp size={18} className="text-brand-500" />
                <h2 className="text-base font-semibold text-gray-800">Status Breakdown</h2>
              </div>
              <div className="space-y-3">
                {statusOrder.map((s) => {
                  const total = data?.total || 1;
                  const pct = Math.round(((counts[s] ?? 0) / total) * 100);
                  return (
                    <div key={s}>
                      <div className="flex justify-between text-xs text-gray-600 mb-1">
                        <StatusBadge status={s} />
                        <span className="font-semibold">{counts[s] ?? 0}</span>
                      </div>
                      <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all"
                          style={{
                            width: `${pct}%`,
                            background: 'linear-gradient(90deg, #7bc2fa 0%, #628ff2 100%)',
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Recent activity */}
            <div className="card">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-base font-semibold text-gray-800">Recent Verifications</h2>
                <Link
                  to="/transcripts"
                  className="text-xs text-brand-500 hover:text-brand-700 font-medium flex items-center gap-1"
                >
                  View all <ArrowRight size={14} />
                </Link>
              </div>
              {recent.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-6">No verifications yet.</p>
              ) : (
                <div className="space-y-3">
                  {recent.map((t) => {
                    const filename = getFilename(t);
                    const mime = ((t.documents?.[0] as Record<string, unknown> | undefined)?.mime_type as string) || '';
                    return (
                      <Link
                        key={t.verification_id}
                        to={`/transcripts/${t.verification_id}`}
                        className="flex items-center justify-between p-3 rounded-lg hover:bg-brand-50 transition-colors group"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <FileIcon mime={mime} />
                          <div className="min-w-0">
                            <p className="text-sm font-medium text-gray-800 truncate">
                              {filename}
                            </p>
                            <p className="text-xs text-gray-400 capitalize">
                              {t.applicant_type.replace('_', ' ')} applicant
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0 ml-3">
                          <StatusBadge status={t.status} />
                          <ArrowRight
                            size={14}
                            className="text-gray-300 group-hover:text-brand-400 transition-colors"
                          />
                        </div>
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
