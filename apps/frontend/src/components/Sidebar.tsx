import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  FileText,
  PlusCircle,
  Users,
  LogOut,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import msbnLogo from '../assets/msbn-logo.png';

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
    <aside
      className="flex flex-col w-64 min-h-screen"
      style={{
        background: '#ffffff',
        borderRight: '1px solid #d5dff0',
        boxShadow: '2px 0 12px rgba(10,22,40,0.07)',
      }}
    >
      {/* Logo */}
      <div
        className="flex items-center justify-center px-5 py-4"
        style={{ background: '#d0daea', borderBottom: '1px solid #bfcfe8' }}
      >
        <img
          src={msbnLogo}
          alt="Mississippi Board of Nursing"
          className="h-12 w-auto object-contain"
          style={{ mixBlendMode: 'multiply' }}
        />
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-5 space-y-0.5">
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                isActive
                  ? 'text-white shadow-md'
                  : 'text-slate-600 hover:text-brand-800 hover:bg-blue-50'
              }`
            }
            style={({ isActive }) =>
              isActive
                ? { background: 'linear-gradient(135deg, #1a3a6b 0%, #2d5fa8 100%)', boxShadow: '0 2px 8px rgba(26,58,107,0.3)' }
                : {}
            }
          >
            <Icon size={17} />
            {label}
          </NavLink>
        ))}

        {isAdmin && (
          <>
            <div className="pt-5 pb-1.5 px-3">
              <p
                className="text-xs font-semibold uppercase tracking-widest"
                style={{ color: '#a0aec0' }}
              >
                Admin
              </p>
            </div>
            {adminItems.map(({ to, icon: Icon, label }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                    isActive
                      ? 'text-white shadow-md'
                      : 'text-slate-600 hover:text-brand-800 hover:bg-blue-50'
                  }`
                }
                style={({ isActive }) =>
                  isActive
                    ? { background: 'linear-gradient(135deg, #1a3a6b 0%, #2d5fa8 100%)', boxShadow: '0 2px 8px rgba(26,58,107,0.3)' }
                    : {}
                }
              >
                <Icon size={17} />
                {label}
              </NavLink>
            ))}
          </>
        )}
      </nav>

      {/* User footer */}
      <div
        className="px-4 py-4"
        style={{ borderTop: '1px solid #d5dff0' }}
      >
        <div className="flex items-center gap-3 mb-3">
          <div
            className="w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-bold flex-shrink-0"
            style={{ background: 'linear-gradient(135deg, #1a3a6b 0%, #2d5fa8 100%)' }}
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
          className="flex items-center gap-2 w-full px-3 py-2 text-sm rounded-lg transition-colors"
          style={{ color: '#718096' }}
          onMouseEnter={e => {
            (e.currentTarget as HTMLButtonElement).style.color = '#e53e3e';
            (e.currentTarget as HTMLButtonElement).style.background = '#fff5f5';
          }}
          onMouseLeave={e => {
            (e.currentTarget as HTMLButtonElement).style.color = '#718096';
            (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
          }}
        >
          <LogOut size={16} />
          Sign out
        </button>
      </div>
    </aside>
  );
}
