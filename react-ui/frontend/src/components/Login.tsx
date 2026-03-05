import { useState } from 'react';
import { login, changePasswordFirstLogin } from '../utils/auth';
import { theme } from '../theme';

interface LoginProps {
  onLoginSuccess: () => void;
}

export function Login({ onLoginSuccess }: LoginProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [requiresPasswordChange, setRequiresPasswordChange] = useState(false);
  const [session, setSession] = useState('');

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const result = await login(email, password);

      if (result.success) {
        onLoginSuccess();
      } else if (result.requiresPasswordChange && result.session) {
        setRequiresPasswordChange(true);
        setSession(result.session);
      } else {
        setError(result.error || 'Login failed');
      }
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  const handlePasswordChange = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (newPassword !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    if (newPassword.length < 12) {
      setError('Password must be at least 12 characters');
      return;
    }

    setLoading(true);

    try {
      const result = await changePasswordFirstLogin(email, password, newPassword, session);

      if (result.success) {
        onLoginSuccess();
      } else {
        setError(result.error || 'Password change failed');
      }
    } catch (err: any) {
      setError(err.message || 'Password change failed');
    } finally {
      setLoading(false);
    }
  };

  if (requiresPasswordChange) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: theme.colors.background,
          padding: theme.spacing.lg,
        }}
      >
        <div
          style={{
            width: '100%',
            maxWidth: '450px',
            backgroundColor: theme.colors.surface,
            borderRadius: theme.borderRadius.lg,
            padding: theme.spacing.xxl,
            boxShadow: theme.elevation.level3,
          }}
        >
          <div style={{ textAlign: 'center', marginBottom: theme.spacing.xl }}>
            <h1
              style={{
                ...theme.typography.headlineLarge,
                color: theme.colors.primary,
                marginBottom: theme.spacing.sm,
              }}
            >
              🔐 Change Password
            </h1>
            <p style={{ ...theme.typography.bodyMedium, color: theme.colors.secondary }}>
              Please set a new password for your first login
            </p>
          </div>

          <form onSubmit={handlePasswordChange}>
            <div style={{ marginBottom: theme.spacing.lg }}>
              <label
                style={{
                  display: 'block',
                  marginBottom: theme.spacing.xs,
                  ...theme.typography.labelLarge,
                  color: theme.colors.onSurface,
                }}
              >
                New Password
              </label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                style={{
                  width: '100%',
                  padding: theme.spacing.md,
                  borderRadius: theme.borderRadius.md,
                  border: `1px solid ${theme.colors.outline}`,
                  ...theme.typography.bodyLarge,
                  boxSizing: 'border-box',
                }}
                placeholder="Min 12 characters"
              />
            </div>

            <div style={{ marginBottom: theme.spacing.lg }}>
              <label
                style={{
                  display: 'block',
                  marginBottom: theme.spacing.xs,
                  ...theme.typography.labelLarge,
                  color: theme.colors.onSurface,
                }}
              >
                Confirm Password
              </label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                style={{
                  width: '100%',
                  padding: theme.spacing.md,
                  borderRadius: theme.borderRadius.md,
                  border: `1px solid ${theme.colors.outline}`,
                  ...theme.typography.bodyLarge,
                  boxSizing: 'border-box',
                }}
                placeholder="Re-enter password"
              />
            </div>

            {error && (
              <div
                style={{
                  padding: theme.spacing.md,
                  backgroundColor: '#FFEBEE',
                  color: '#C62828',
                  borderRadius: theme.borderRadius.md,
                  marginBottom: theme.spacing.lg,
                  ...theme.typography.bodyMedium,
                }}
              >
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              style={{
                width: '100%',
                padding: theme.spacing.md,
                backgroundColor: theme.colors.primary,
                color: theme.colors.onPrimary,
                border: 'none',
                borderRadius: theme.borderRadius.md,
                ...theme.typography.labelLarge,
                cursor: loading ? 'not-allowed' : 'pointer',
                opacity: loading ? 0.6 : 1,
              }}
            >
              {loading ? 'Changing Password...' : 'Change Password'}
            </button>
          </form>

          <div
            style={{
              marginTop: theme.spacing.lg,
              padding: theme.spacing.md,
              backgroundColor: theme.colors.surfaceVariant,
              borderRadius: theme.borderRadius.md,
              ...theme.typography.bodyMedium,
              color: theme.colors.secondary,
            }}
          >
            <strong>Password requirements:</strong>
            <ul style={{ marginTop: theme.spacing.xs, paddingLeft: theme.spacing.lg }}>
              <li>At least 12 characters</li>
              <li>Include uppercase and lowercase letters</li>
              <li>Include numbers and symbols</li>
            </ul>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: theme.colors.background,
        padding: theme.spacing.lg,
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: '400px',
          backgroundColor: theme.colors.surface,
          borderRadius: theme.borderRadius.lg,
          padding: theme.spacing.xxl,
          boxShadow: theme.elevation.level3,
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: theme.spacing.xl }}>
          <h1
            style={{
              ...theme.typography.headlineLarge,
              color: theme.colors.primary,
              marginBottom: theme.spacing.sm,
            }}
          >
            🌍 Geospatial Agent on AWS
          </h1>
          <p style={{ ...theme.typography.bodyMedium, color: theme.colors.secondary }}>
            Satellite Image Analysis
          </p>
        </div>

        <form onSubmit={handleLogin}>
          <div style={{ marginBottom: theme.spacing.lg }}>
            <label
              style={{
                display: 'block',
                marginBottom: theme.spacing.xs,
                ...theme.typography.labelLarge,
                color: theme.colors.onSurface,
              }}
            >
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
              style={{
                width: '100%',
                padding: theme.spacing.md,
                borderRadius: theme.borderRadius.md,
                border: `1px solid ${theme.colors.outline}`,
                ...theme.typography.bodyLarge,
                boxSizing: 'border-box',
              }}
              placeholder="your@email.com"
            />
          </div>

          <div style={{ marginBottom: theme.spacing.lg }}>
            <label
              style={{
                display: 'block',
                marginBottom: theme.spacing.xs,
                ...theme.typography.labelLarge,
                color: theme.colors.onSurface,
              }}
            >
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              style={{
                width: '100%',
                padding: theme.spacing.md,
                borderRadius: theme.borderRadius.md,
                border: `1px solid ${theme.colors.outline}`,
                ...theme.typography.bodyLarge,
                boxSizing: 'border-box',
              }}
              placeholder="••••••••••••"
            />
          </div>

          {error && (
            <div
              style={{
                padding: theme.spacing.md,
                backgroundColor: '#FFEBEE',
                color: '#C62828',
                borderRadius: theme.borderRadius.md,
                marginBottom: theme.spacing.lg,
                ...theme.typography.bodyMedium,
              }}
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              padding: theme.spacing.md,
              backgroundColor: theme.colors.primary,
              color: theme.colors.onPrimary,
              border: 'none',
              borderRadius: theme.borderRadius.md,
              ...theme.typography.labelLarge,
              cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        <div
          style={{
            marginTop: theme.spacing.lg,
            textAlign: 'center',
            ...theme.typography.bodyMedium,
            color: theme.colors.secondary,
          }}
        >
          Contact your administrator for access
        </div>
      </div>
    </div>
  );
}
