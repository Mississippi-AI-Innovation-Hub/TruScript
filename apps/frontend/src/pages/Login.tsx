import { useState, type FormEvent } from 'react';
import { Navigate } from 'react-router-dom';
import { Eye, EyeOff } from 'lucide-react';
import toast from 'react-hot-toast';
import { useAuth } from '../context/AuthContext';
import { Spinner } from '../components/Spinner';
import msbnLogo from '../assets/msbn-logo.png';

export function Login() {
  const { isAuthenticated, login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);

  if (isAuthenticated) return <Navigate to="/dashboard" replace />;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      await login({ username, password });
      toast.success('Welcome back!');
    } catch {
      toast.error('Invalid username or password.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ background: '#e8edf6' }}
    >

      <div className="w-full max-w-md relative z-10">

        {/* Logo block */}
        <div className="text-center mb-8">
          <div
            className="inline-flex items-center justify-center px-8 py-5 mb-4 rounded-2xl"
            style={{ background: '#d0daea' }}
          >
            <img
              src={msbnLogo}
              alt="Mississippi Board of Nursing"
              className="h-14 w-auto object-contain"
              style={{ mixBlendMode: 'multiply' }}
            />
          </div>
          <p
            className="text-sm tracking-widest uppercase font-medium mt-1"
            style={{ color: '#8a9bb5', letterSpacing: '0.18em' }}
          >
            Transcript Verification System
          </p>
        </div>

        {/* Card */}
        <div
          className="rounded-2xl p-8"
          style={{
            background: 'rgba(255,255,255,0.97)',
            boxShadow: '0 24px 60px rgba(0,0,0,0.35), 0 4px 16px rgba(0,0,0,0.2)',
          }}
        >
          <h2 className="text-xl font-bold mb-1" style={{ color: '#0f2347' }}>
            Sign in to your account
          </h2>
          <p className="text-sm text-gray-400 mb-6">Enter your credentials to continue</p>

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label htmlFor="username" className="label">Username</label>
              <input
                id="username"
                type="text"
                className="input"
                placeholder="Enter your username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoFocus
                autoComplete="username"
              />
            </div>

            <div>
              <label htmlFor="password" className="label">Password</label>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  className="input pr-10"
                  placeholder="Enter your password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                  tabIndex={-1}
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg font-semibold text-white transition-opacity disabled:opacity-60"
              style={{ background: 'linear-gradient(135deg, #1a3a6b 0%, #2563b0 100%)', boxShadow: '0 4px 14px rgba(26,58,107,0.4)' }}
            >
              {loading ? <Spinner size={18} /> : null}
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>

          <p className="text-xs text-gray-400 text-center mt-6">
            Access is restricted to authorized MSBN personnel only.
            <br />Contact your administrator for account creation.
          </p>
        </div>

        {/* Footer */}
        <p className="text-center mt-6 text-xs" style={{ color: '#8a9bb5' }}>
          © {new Date().getFullYear()} Mississippi Board of Nursing. All rights reserved.
        </p>
      </div>
    </div>
  );
}
