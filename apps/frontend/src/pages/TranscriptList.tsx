import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { PlusCircle, ChevronLeft, ChevronRight, ArrowRight, Search } from 'lucide-react';
import { transcriptsApi } from '../api';
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

export function TranscriptList() {
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState('');

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['transcripts', page],
    queryFn: () => transcriptsApi.list(page * PAGE_SIZE, PAGE_SIZE),
    placeholderData: (prev) => prev,
  });

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;

  const filtered = search
    ? (data?.items ?? []).filter(
        (t) =>
          t.applicant_id.toLowerCase().includes(search.toLowerCase()) ||
          t.verification_id.toLowerCase().includes(search.toLowerCase())
      )
    : (data?.items ?? []);

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
          placeholder="Search by applicant ID or verification ID…"
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
                    {['Verification ID', 'Applicant ID', 'Type', 'Status', 'Assigned Staff', 'Created', ''].map(
                      (h) => (
                        <th
                          key={h}
                          className="px-6 py-3 text-left text-xs font-semibold text-brand-700 uppercase tracking-wider"
                        >
                          {h}
                        </th>
                      )
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {filtered.map((t) => (
                    <tr key={t.verification_id} className="hover:bg-brand-50/40 transition-colors">
                      <td className="px-6 py-4 text-xs font-mono text-gray-500 max-w-[160px] truncate">
                        {t.verification_id}
                      </td>
                      <td className="px-6 py-4 text-sm font-medium text-gray-800">
                        {t.applicant_id}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500 capitalize">
                        {t.applicant_type.replace('_', ' ')}
                      </td>
                      <td className="px-6 py-4">
                        <StatusBadge status={t.status} />
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500">
                        {t.assigned_staff_id ?? <span className="text-gray-300 italic">Unassigned</span>}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-400">
                        {formatDate(t.created_at)}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <Link
                          to={`/transcripts/${t.verification_id}`}
                          className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-800"
                        >
                          View <ArrowRight size={13} />
                        </Link>
                      </td>
                    </tr>
                  ))}
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
