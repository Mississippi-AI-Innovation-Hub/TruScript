import { Outlet, Navigate } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { useAuth } from '../context/AuthContext';

export function Layout() {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen" style={{ background: '#e8edf6' }}>
        <div
          className="w-10 h-10 rounded-full border-4 border-transparent animate-spin"
          style={{ borderTopColor: '#1a3a6b' }}
        />
      </div>
    );
  }

  if (!isAuthenticated) return <Navigate to="/login" replace />;

  return (
    <div className="flex min-h-screen" style={{ background: '#e8edf6' }}>
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
