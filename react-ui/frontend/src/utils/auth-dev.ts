/**
 * Development Authentication Mode
 *
 * This file provides a mock authentication system for local development
 * when you don't want to set up Cognito locally.
 *
 * To enable dev mode:
 * 1. Set VITE_DEV_MODE=true in your .env file
 * 2. Restart your dev server
 * 3. You'll be logged in automatically as dev@example.com
 *
 * WARNING: Never use this in production!
 */

const DEV_MODE = import.meta.env.VITE_DEV_MODE === 'true';

export interface AuthTokens {
  idToken: string;
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
}

export interface AuthUser {
  email: string;
  sub: string;
}

const DEV_USER: AuthUser = {
  email: 'dev@example.com',
  sub: 'dev-user-12345',
};

const DEV_TOKENS: AuthTokens = {
  idToken: 'dev-id-token',
  accessToken: 'dev-access-token',
  refreshToken: 'dev-refresh-token',
  expiresAt: Date.now() + 365 * 24 * 60 * 60 * 1000, // 1 year from now
};

/**
 * Check if running in dev mode
 */
export function isDevMode(): boolean {
  return DEV_MODE;
}

/**
 * Mock login (instant success in dev mode)
 */
export async function loginDev(): Promise<{ success: boolean }> {
  if (!DEV_MODE) {
    throw new Error('Dev mode not enabled');
  }

  localStorage.setItem('auth_tokens', JSON.stringify(DEV_TOKENS));
  localStorage.setItem('auth_user', JSON.stringify(DEV_USER));
  console.log('🔧 DEV MODE: Logged in as', DEV_USER.email);

  return { success: true };
}

/**
 * Get dev tokens
 */
export function getDevTokens(): AuthTokens | null {
  if (!DEV_MODE) return null;

  const stored = localStorage.getItem('auth_tokens');
  return stored ? JSON.parse(stored) : null;
}

/**
 * Get dev user
 */
export function getDevUser(): AuthUser | null {
  if (!DEV_MODE) return null;

  const stored = localStorage.getItem('auth_user');
  return stored ? JSON.parse(stored) : DEV_USER;
}

/**
 * Check if authenticated in dev mode
 */
export function isAuthenticatedDev(): boolean {
  if (!DEV_MODE) return false;
  return localStorage.getItem('auth_tokens') !== null;
}

/**
 * Mock logout
 */
export async function logoutDev(): Promise<void> {
  if (!DEV_MODE) return;

  localStorage.removeItem('auth_tokens');
  localStorage.removeItem('auth_user');
  console.log('🔧 DEV MODE: Logged out');
}

/**
 * Initialize dev mode (auto-login)
 */
export function initDevMode(): void {
  if (!DEV_MODE) return;

  if (!localStorage.getItem('auth_tokens')) {
    localStorage.setItem('auth_tokens', JSON.stringify(DEV_TOKENS));
    localStorage.setItem('auth_user', JSON.stringify(DEV_USER));
    console.log('🔧 DEV MODE ACTIVE: Auto-logged in as', DEV_USER.email);
    console.log('🔧 To disable, remove VITE_DEV_MODE from .env');
  }
}
