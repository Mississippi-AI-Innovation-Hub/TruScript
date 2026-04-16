import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { PlusCircle, ChevronLeft, ChevronRight, ArrowRight, Search, FileText, User } from 'lucide-react';
import { transcriptsApi, authApi } from '../api';
import type { TranscriptResponse, UserResponse } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { PageHeader } from '../components/PageHeader';
import { Spinner } from '../components/Spinner';

const PAGE_SIZE = 20;

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

/** Pull the original filename from the documents array. */
function getFilename(t: TranscriptResponse): string {
  const doc = t.documents?.[0] as Record<string, unknown> | undefined;
  const name = doc?.original_filename as string | undefined;
  return name || '—';
}

/** Build id → display name map from staff list. */
function buildStaffMap(staff: UserResponse[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const s of staff) {
    const name =
      s.first_name && s.last_name
        ? `${s.first_name} ${s.last_name}`
        : s.username;
    map.set(s.id, name);
  }
  return map;
}

export function TranscriptList() {
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState('');

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['transcripts', page],
    queryFn: () => transcriptsApi.list(page * PAGE_SIZE, PAGE_SIZE),
    placeholderData: (prev) => prev,
  });

  // Fetch staff for name resolution — stale 5 min, won't block the page
  const { data: staffList = [] } = useQuery({
    queryKey: ['staff-list'],
    queryFn: () => authApi.listStaff(),
    staleTime: 5 * 60 * 1000,
  });

  const staffMap = useMemo(() => buildStaffMap(staffList), [staffList]);

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;

  const filtered = useMemo(() => {
    const items = data?.items ?? [];
    if (!search) return items;
    const q = search.toLowerCase();
    return items.filter((t) => {
      const filename = getFilename(t).toLowerCase();
      const staffName = t.assigned_staff_id
        ? (staffMap.get(t.assigned_staff_id) ?? '').toLowerCase()
        : '';
      return (
        filename.includes(q) ||
        t.verification_id.toLowerCase().includes(q) ||
        t.status.toLowerCase().includes(q) ||
        staffName.includes(q)
      );
    });
  }, [data, search, staffMap]);

  return (
    <div className="p-8">
      <PageHeader
        title="Transcript Verifications"
        subtitle="Review, manage, and track all nursing transcript verification records."
        actions={
          <Link to="/transcripts/new" className="btn-primary flex items-center gap-2 text-sm">
            <PlusCircle size={16} />
            New Verification
          </Link>
        }
      />

      {/* Search */}
      <div className="relative mb-5 max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          type="text"
          className="input pl-9"
          placeholder="Search by filename, status, or reviewer…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Table card */}
      <div className="card p-0 overflow-hidden">
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size={32} /></div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-16 text-gray-400">
            <p className="text-lg font-medium">No verifications found.</p>
            <p className="text-sm mt-1">
              {search ? 'Try a different search term.' : 'Create the first one using the button above.'}
            </p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-100">
                <thead className="bg-brand-50">
                  <tr>
                    {['#', 'Transcript', 'Type', 'Status', 'Assigned Reviewer', 'Created', ''].map((h) => (
                      <th
                        key={h}
                        className="px-6 py-3 text-left text-xs font-semibold text-brand-700 uppercase tracking-wider"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {filtered.map((t, idx) => {
                    const globalIndex = page * PAGE_SIZE + idx + 1;
                    const filename = getFilename(t);
                    const staffName = t.assigned_staff_id
                      ? staffMap.get(t.assigned_staff_id)
                      : null;

                    return (
                      <tr key={t.verification_id} className="hover:bg-brand-50/40 transition-colors">

                        {/* # — sequential count */}
                        <td className="px-6 py-4 text-sm font-semibold text-gray-400 w-12">
                          #{globalIndex}
                        </td>

                        {/* Transcript filename */}
                        <td className="px-6 py-4 max-w-[240px]">
                          <div className="flex items-center gap-2">
                            <FileText size={15} className="text-gray-400 shrink-0" />
                            <span
                              className="text-sm font-medium text-gray-800 truncate"
                              title={filename}
                            >
                              {filename}
                            </span>
                          </div>
                        </td>

                        {/* Applicant type */}
                        <td className="px-6 py-4 text-sm text-gray-500 capitalize whitespace-nowrap">
                          {t.applicant_type.replace('_', ' ')}
                        </td>

                        {/* Status */}
                        <td className="px-6 py-4">
                          <StatusBadge status={t.status} />
                        </td>

                        {/* Assigned reviewer — name, not ID */}
                        <td className="px-6 py-4 text-sm text-gray-700 whitespace-nowrap">
                          {staffName ? (
                            <div className="flex items-center gap-1.5">
                              <User size={13} className="text-gray-400 shrink-0" />
                              <span>{staffName}</span>
                            </div>
                          ) : (
                            <span className="text-gray-300 italic">Unassigned</span>
                          )}
                        </td>

                        {/* Created date */}
                        <td className="px-6 py-4 text-sm text-gray-400 whitespace-nowrap">
                          {formatDate(t.created_at)}
                        </td>

                        {/* View link */}
                        <td className="px-6 py-4 text-right">
                          <Link
                            to={`/transcripts/${t.verification_id}`}
                            className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-800"
                          >
                            View <ArrowRight size={13} />
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalPages > 1 && !search && (
              <div className="flex items-center justify-between px-6 py-4 border-t border-gray-100 bg-gray-50">
                <p className="text-sm text-gray-500">
                  Page {page + 1} of {totalPages} &nbsp;·&nbsp; {data?.total} total
                </p>
                <div className="flex gap-2">
                  <button
                    disabled={page === 0}
                    onClick={() => setPage((p) => p - 1)}
                    className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1 disabled:opacity-40"
                  >
                    <ChevronLeft size={14} /> Prev
                  </button>
                  <button
                    disabled={page >= totalPages - 1 || isFetching}
                    onClick={() => setPage((p) => p + 1)}
                    className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1 disabled:opacity-40"
                  >
                    Next <ChevronRight size={14} />
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
