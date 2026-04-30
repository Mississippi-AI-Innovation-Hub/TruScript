import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  PlusCircle,
  ChevronLeft,
  ChevronRight,
  ArrowRight,
  Search,
  FileText,
  User,
  Trash2,
  AlertTriangle,
} from 'lucide-react';
import { transcriptsApi, authApi } from '../api';
import type { TranscriptResponse, UserResponse } from '../types';
import { useAuth } from '../context/AuthContext';
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

function getFilename(t: TranscriptResponse): string {
  const doc = t.documents?.[0] as Record<string, unknown> | undefined;
  const name = doc?.original_filename as string | undefined;
  return name || '—';
}

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

// ── Confirmation modal ────────────────────────────────────────────────────────
interface ConfirmDeleteModalProps {
  count: number;
  onConfirm: () => void;
  onCancel: () => void;
  isDeleting: boolean;
}

function ConfirmDeleteModal({ count, onConfirm, onCancel, isDeleting }: ConfirmDeleteModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 p-6">
        <div className="flex items-start gap-4">
          <div className="flex-shrink-0 bg-red-100 rounded-full p-2.5">
            <AlertTriangle size={22} className="text-red-600" />
          </div>
          <div className="flex-1">
            <h2 className="text-base font-semibold text-gray-900">
              Delete {count === 1 ? 'transcript' : `${count} transcripts`}?
            </h2>
            <p className="mt-1.5 text-sm text-gray-500">
              This will permanently remove{' '}
              {count === 1 ? 'this transcript' : `these ${count} transcripts`} and all
              associated documents, audit results, and flags. This action cannot be undone.
            </p>
          </div>
        </div>
        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onCancel}
            disabled={isDeleting}
            className="btn-secondary text-sm px-4 py-2"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={isDeleting}
            className="flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors disabled:opacity-60"
          >
            {isDeleting ? <Spinner size={14} /> : <Trash2 size={14} />}
            {isDeleting ? 'Deleting…' : `Delete ${count === 1 ? '' : count + ' '}transcript${count === 1 ? '' : 's'}`}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export function TranscriptList() {
  const { isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState('');

  // Selection state
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Delete confirmation state
  const [pendingDelete, setPendingDelete] = useState<string[] | null>(null); // null = closed
  const [isDeleting, setIsDeleting] = useState(false);

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['transcripts', page],
    queryFn: () => transcriptsApi.list(page * PAGE_SIZE, PAGE_SIZE),
    placeholderData: (prev) => prev,
  });

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

  // ── Selection helpers ────────────────────────────────────────────────────
  const allFilteredIds = filtered.map((t) => t.verification_id);
  const allSelected =
    allFilteredIds.length > 0 && allFilteredIds.every((id) => selected.has(id));
  const someSelected = allFilteredIds.some((id) => selected.has(id));

  function toggleAll() {
    if (allSelected) {
      setSelected((prev) => {
        const next = new Set(prev);
        allFilteredIds.forEach((id) => next.delete(id));
        return next;
      });
    } else {
      setSelected((prev) => {
        const next = new Set(prev);
        allFilteredIds.forEach((id) => next.add(id));
        return next;
      });
    }
  }

  function toggleOne(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // ── Delete handlers ─────────────────────────────────────────────────────
  function requestDelete(ids: string[]) {
    setPendingDelete(ids);
  }

  async function confirmDelete() {
    if (!pendingDelete || pendingDelete.length === 0) return;
    setIsDeleting(true);
    try {
      if (pendingDelete.length === 1) {
        await transcriptsApi.delete(pendingDelete[0]);
      } else {
        await transcriptsApi.bulkDelete(pendingDelete);
      }
      // Clear selection for deleted items
      setSelected((prev) => {
        const next = new Set(prev);
        pendingDelete.forEach((id) => next.delete(id));
        return next;
      });
      // Invalidate list cache
      await queryClient.invalidateQueries({ queryKey: ['transcripts'] });
    } catch (err) {
      console.error('Delete failed:', err);
    } finally {
      setIsDeleting(false);
      setPendingDelete(null);
    }
  }

  const selectedOnPage = allFilteredIds.filter((id) => selected.has(id));

  return (
    <div className="p-8">
      {/* Confirmation modal */}
      {pendingDelete && (
        <ConfirmDeleteModal
          count={pendingDelete.length}
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
          isDeleting={isDeleting}
        />
      )}

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

      {/* Toolbar — search + bulk delete */}
      <div className="flex items-center gap-3 mb-5">
        <div className="relative max-w-sm flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            className="input pl-9 w-full"
            placeholder="Search by filename, status, or reviewer…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {isAdmin && selectedOnPage.length > 0 && (
          <button
            onClick={() => requestDelete(selectedOnPage)}
            className="flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
          >
            <Trash2 size={15} />
            Delete selected ({selectedOnPage.length})
          </button>
        )}
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
                    {/* Select-all checkbox — admin only */}
                    {isAdmin && (
                      <th className="px-4 py-3 w-10">
                        <input
                          type="checkbox"
                          checked={allSelected}
                          ref={(el) => {
                            if (el) el.indeterminate = someSelected && !allSelected;
                          }}
                          onChange={toggleAll}
                          className="w-4 h-4 rounded border-gray-300 text-brand-600 cursor-pointer"
                        />
                      </th>
                    )}
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
                    const isChecked = selected.has(t.verification_id);

                    return (
                      <tr
                        key={t.verification_id}
                        className={`hover:bg-brand-50/40 transition-colors ${isAdmin && isChecked ? 'bg-red-50/40' : ''}`}
                      >
                        {/* Row checkbox — admin only */}
                        {isAdmin && (
                          <td className="px-4 py-4 w-10">
                            <input
                              type="checkbox"
                              checked={isChecked}
                              onChange={() => toggleOne(t.verification_id)}
                              className="w-4 h-4 rounded border-gray-300 text-brand-600 cursor-pointer"
                            />
                          </td>
                        )}

                        {/* # */}
                        <td className="px-6 py-4 text-sm font-semibold text-gray-400 w-12">
                          #{globalIndex}
                        </td>

                        {/* Filename */}
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

                        {/* Assigned reviewer */}
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

                        {/* Actions: View + Delete (delete admin only) */}
                        <td className="px-6 py-4 text-right">
                          <div className="flex items-center justify-end gap-3">
                            <Link
                              to={`/transcripts/${t.verification_id}`}
                              className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-800"
                            >
                              View <ArrowRight size={13} />
                            </Link>
                            {isAdmin && (
                              <button
                                onClick={() => requestDelete([t.verification_id])}
                                title="Delete transcript"
                                className="p-1 text-gray-300 hover:text-red-500 transition-colors rounded"
                              >
                                <Trash2 size={15} />
                              </button>
                            )}
                          </div>
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
