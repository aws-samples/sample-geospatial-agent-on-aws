import { Link, useLocation, useNavigate } from 'react-router-dom';
import { theme } from '../theme';
import { logout } from '../utils/auth';

export function Navigation() {
  const location = useLocation();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const navItems = [
    { path: '/', label: 'Chat' },
    { path: '/use-cases', label: 'Use Case Gallery' },
    { path: '/technology', label: 'Technology' },
  ];

  return (
    <nav
      style={{
        backgroundColor: theme.colors.primary,
        color: theme.colors.onPrimary,
        boxShadow: theme.elevation.level2,
        position: 'relative',
        zIndex: 100,
      }}
    >
      <div
        style={{
          padding: `0 ${theme.spacing.lg}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          height: '64px',
        }}
      >
        <h1
          style={{
            margin: 0,
            fontSize: '20px',
            fontWeight: 500,
            letterSpacing: '0.15px',
            display: 'flex',
            alignItems: 'center',
            gap: theme.spacing.sm,
          }}
        >
          Geospatial Agent on AWS
        </h1>

        <div style={{ display: 'flex', alignItems: 'center', gap: theme.spacing.xl }}>
          <div style={{ display: 'flex', gap: theme.spacing.sm }}>
            {navItems.map((item) => {
            const isActive = location.pathname === item.path;
            return (
              <Link
                key={item.path}
                to={item.path}
                style={{
                  position: 'relative',
                  padding: `${theme.spacing.md} ${theme.spacing.lg}`,
                  color: theme.colors.onPrimary,
                  textDecoration: 'none',
                  fontSize: '14px',
                  fontWeight: 500,
                  letterSpacing: '0.1px',
                  transition: theme.transitions.short,
                  backgroundColor: isActive
                    ? 'rgba(255, 255, 255, 0.12)'
                    : 'transparent',
                  borderRadius: theme.borderRadius.md,
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.08)';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.backgroundColor = 'transparent';
                  }
                }}
              >
                {item.label}
                {isActive && (
                  <div
                    style={{
                      position: 'absolute',
                      bottom: 0,
                      left: theme.spacing.lg,
                      right: theme.spacing.lg,
                      height: '3px',
                      backgroundColor: theme.colors.onPrimary,
                      borderRadius: '3px 3px 0 0',
                    }}
                  />
                )}
              </Link>
            );
          })}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: theme.spacing.md }}>
            <button
              onClick={handleLogout}
              style={{
                padding: `${theme.spacing.sm} ${theme.spacing.md}`,
                backgroundColor: 'rgba(255, 255, 255, 0.15)',
                color: theme.colors.onPrimary,
                border: 'none',
                borderRadius: theme.borderRadius.md,
                fontSize: '14px',
                fontWeight: 500,
                cursor: 'pointer',
                transition: theme.transitions.short,
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.25)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.15)';
              }}
            >
              Sign Out
            </button>
            <img
              src="/AWS_logo_RGB_1c_White.png"
              alt="AWS Logo"
              style={{
                height: '32px',
                width: 'auto',
              }}
            />
          </div>
        </div>
      </div>
    </nav>
  );
}
