import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  FileText,
  PlusCircle,
  Users,
  LogOut,
  ShieldCheck,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const navItems = [
  { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/transcripts', icon: FileText, label: 'Verifications' },
  { to: '/transcripts/new', icon: PlusCircle, label: 'New Verification' },
];

const adminItems = [
  { to: '/admin/users', icon: Users, label: 'User Management' },
];

export function Sidebar() {
  const { user, isAdmin, logout } = useAuth();

  return (
    <aside className="flex flex-col w-64 min-h-screen bg-white border-r border-brand-100 shadow-sm">
      {/* Logo */}
      <div
        className="flex items-center gap-3 px-6 py-5 border-b border-brand-100"
        style={{ background: 'linear-gradient(0deg, #7bc2fa 0%, #628ff2 100%)' }}
      >
        <ShieldCheck className="text-white" size={28} strokeWidth={2} />
        <div>
          <p className="text-white font-bold text-sm leading-tight">MSBN</p>
          <p className="text-white/80 text-xs leading-tight">Transcript Verification</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                isActive
                  ? 'bg-brand-gradient text-white shadow-sm'
                  : 'text-gray-600 hover:bg-brand-50 hover:text-brand-700'
              }`
            }
            style={({ isActive }) =>
              isActive ? { background: 'linear-gradient(0deg, #7bc2fa 0%, #628ff2 100%)' } : {}
            }
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}

        {isAdmin && (
          <>
            <div className="pt-4 pb-1 px-3">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Admin</p>
            </div>
            {adminItems.map(({ to, icon: Icon, label }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                    isActive
                      ? 'text-white shadow-sm'
                      : 'text-gray-600 hover:bg-brand-50 hover:text-brand-700'
                  }`
                }
                style={({ isActive }) =>
                  isActive ? { background: 'linear-gradient(0deg, #7bc2fa 0%, #628ff2 100%)' } : {}
                }
              >
                <Icon size={18} />
                {label}
              </NavLink>
            ))}
          </>
        )}
      </nav>

      {/* User footer */}
      <div className="border-t border-brand-100 px-4 py-4">
        <div className="flex items-center gap-3 mb-3">
          <div
            className="w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-bold flex-shrink-0"
            style={{ background: 'linear-gradient(0deg, #7bc2fa 0%, #628ff2 100%)' }}
          >
            {(user?.name ?? user?.username ?? 'U')[0].toUpperCase()}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-gray-800 truncate">
              {user?.name ?? user?.username}
            </p>
            <p className="text-xs text-gray-400 truncate">{user?.email ?? ''}</p>
          </div>
        </div>
        <button
          onClick={logout}
          className="flex items-center gap-2 w-full px-3 py-2 text-sm text-gray-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
        >
          <LogOut size={16} />
          Sign out
        </button>
      </div>
    </aside>
  );
}
