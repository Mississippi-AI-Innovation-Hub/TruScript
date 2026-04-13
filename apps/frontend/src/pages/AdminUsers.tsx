import { useState, type FormEvent } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { UserPlus, Search, CheckCircle, XCircle } from 'lucide-react';
import toast from 'react-hot-toast';
import { authApi } from '../api';
import { useAuth } from '../context/AuthContext';
import { Navigate } from 'react-router-dom';
import { PageHeader } from '../components/PageHeader';
import { Spinner } from '../components/Spinner';
import type { UserRole } from '../types';

function RoleBadge({ roles }: { roles: string[] }) {
  const isAdmin = roles.includes('msbn-admin');
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${
        isAdmin
          ? 'bg-purple-100 text-purple-800 border-purple-200'
          : 'bg-brand-100 text-brand-800 border-brand-200'
      }`}
    >
      {isAdmin ? 'Admin' : 'Staff'}
    </span>
  );
}

export function AdminUsers() {
  const { isAdmin } = useAuth();
  const queryClient = useQueryClient();

  const [search, setSearch] = useState('');
  const [showForm, setShowForm] = useState(false);

  // Form state
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<UserRole>('msbn-staff');

  if (!isAdmin) return <Navigate to="/dashboard" replace />;

  const { data: users, isLoading } = useQuery({
    queryKey: ['users', search],
    queryFn: () => authApi.listUsers(search || undefined),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      authApi.createUser({ username, email, first_name: firstName, last_name: lastName, password, role }),
    onSuccess: () => {
      toast.success(`User "${username}" created.`);
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setShowForm(false);
      setUsername(''); setEmail(''); setFirstName(''); setLastName(''); setPassword('');
      setRole('msbn-staff');
    },
    onError: () => toast.error('Failed to create user.'),
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    createMutation.mutate();
  }

  return (
    <div className="p-8">
      <PageHeader
        title="User Management"
        subtitle="Manage MSBN staff and admin accounts."
        actions={
          <button
            onClick={() => setShowForm((v) => !v)}
            className="btn-primary flex items-center gap-2 text-sm"
          >
            <UserPlus size={16} />
            {showForm ? 'Cancel' : 'New User'}
          </button>
        }
      />

      {/* Create user form */}
      {showForm && (
        <div className="card mb-6 border-brand-200">
          <h2 className="text-base font-semibold text-gray-800 mb-5">Create New User</h2>
          <form onSubmit={handleSubmit} className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">First Name <span className="text-red-400">*</span></label>
              <input className="input" placeholder="Jane" value={firstName} onChange={(e) => setFirstName(e.target.value)} required />
            </div>
            <div>
              <label className="label">Last Name <span className="text-red-400">*</span></label>
              <input className="input" placeholder="Doe" value={lastName} onChange={(e) => setLastName(e.target.value)} required />
            </div>
            <div>
              <label className="label">Username <span className="text-red-400">*</span></label>
              <input className="input" placeholder="jane_doe" value={username} onChange={(e) => setUsername(e.target.value)} required minLength={3} maxLength={64} />
            </div>
            <div>
              <label className="label">Email <span className="text-red-400">*</span></label>
              <input className="input" type="email" placeholder="jane@msbn.gov" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </div>
            <div>
              <label className="label">Password <span className="text-red-400">*</span></label>
              <input className="input" type="password" placeholder="Min 8 characters" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
              <p className="text-xs text-gray-400 mt-1">User must change password on first login.</p>
            </div>
            <div>
              <label className="label">Role</label>
              <select className="input" value={role} onChange={(e) => setRole(e.target.value as UserRole)}>
                <option value="msbn-staff">MSBN Staff</option>
                <option value="msbn-admin">MSBN Admin</option>
              </select>
            </div>
            <div className="col-span-2 flex gap-3 pt-2">
              <button
                type="submit"
                disabled={createMutation.isPending}
                className="btn-primary text-sm flex items-center gap-2 py-2.5 px-5"
              >
                {createMutation.isPending ? <Spinner size={16} /> : <UserPlus size={16} />}
                {createMutation.isPending ? 'Creating…' : 'Create User'}
              </button>
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="btn-secondary text-sm py-2.5 px-5"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Search */}
      <div className="relative mb-5 max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          type="text"
          className="input pl-9"
          placeholder="Search by username, email, or name…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Users table */}
      <div className="card p-0 overflow-hidden">
        {isLoading ? (
          <div className="flex justify-center py-16"><Spinner size={32} /></div>
        ) : !users || users.length === 0 ? (
          <div className="text-center py-16 text-gray-400">
            <p className="text-lg font-medium">No users found.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-100">
              <thead className="bg-brand-50">
                <tr>
                  {['Name', 'Username', 'Email', 'Role', 'Status'].map((h) => (
                    <th key={h} className="px-6 py-3 text-left text-xs font-semibold text-brand-700 uppercase tracking-wider">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {users.map((u) => (
                  <tr key={u.id} className="hover:bg-brand-50/40 transition-colors">
                    <td className="px-6 py-4 text-sm font-medium text-gray-800">
                      {[u.first_name, u.last_name].filter(Boolean).join(' ') || '—'}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-600">{u.username}</td>
                    <td className="px-6 py-4 text-sm text-gray-500">{u.email ?? '—'}</td>
                    <td className="px-6 py-4">
                      <RoleBadge roles={u.roles} />
                    </td>
                    <td className="px-6 py-4">
                      {u.enabled ? (
                        <span className="inline-flex items-center gap-1 text-xs text-green-700">
                          <CheckCircle size={13} /> Active
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs text-red-500">
                          <XCircle size={13} /> Disabled
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
