/**
 * Cognito Authentication Utilities
 *
 * Handles user authentication with AWS Cognito
 * - Login with email/password
 * - Token management (ID token, access token, refresh token)
 * - Automatic token refresh
 * - Secure password change on first login
 *
 * For local development without Cognito, see auth-dev.ts
 */

import {
  isDevMode,
  loginDev,
  getDevTokens,
  getDevUser,
  isAuthenticatedDev,
  logoutDev,
  initDevMode,
} from './auth-dev';

import {
  CognitoIdentityProviderClient,
  InitiateAuthCommand,
  RespondToAuthChallengeCommand,
  GlobalSignOutCommand,
  AuthFlowType,
  ChallengeNameType,
} from '@aws-sdk/client-cognito-identity-provider';

// Get Cognito configuration from environment or runtime config
let COGNITO_USER_POOL_ID = import.meta.env.VITE_COGNITO_USER_POOL_ID;
let COGNITO_CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID;
let COGNITO_REGION = import.meta.env.VITE_COGNITO_REGION || 'us-east-1';

// Runtime config cache
let configLoaded = false;
let configPromise: Promise<void> | null = null;

// Fetch runtime config from backend (for production deployments)
async function loadRuntimeConfig(): Promise<void> {
  if (configLoaded) return;
  if (configPromise) return configPromise;

  configPromise = (async () => {
    try {
      const response = await fetch('/api/config');
      if (response.ok) {
        const config = await response.json();
        if (config.cognito) {
          COGNITO_USER_POOL_ID = config.cognito.userPoolId || COGNITO_USER_POOL_ID;
          COGNITO_CLIENT_ID = config.cognito.clientId || COGNITO_CLIENT_ID;
          COGNITO_REGION = config.cognito.region || COGNITO_REGION;
        }
      }
    } catch {
      // Failed to load runtime config, using environment variables
    } finally {
      configLoaded = true;
      configPromise = null;
    }
  })();

  return configPromise;
}

// Initialize config loading
if (!COGNITO_USER_POOL_ID || !COGNITO_CLIENT_ID) {
  loadRuntimeConfig();
}

let cognitoClient: CognitoIdentityProviderClient | null = null;

function getCognitoClient(): CognitoIdentityProviderClient {
  if (!cognitoClient || cognitoClient.config.region !== COGNITO_REGION) {
    cognitoClient = new CognitoIdentityProviderClient({
      region: COGNITO_REGION,
    });
  }
  return cognitoClient;
}

export interface AuthTokens {
  idToken: string;
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
}

export interface AuthUser {
  email: string;
  sub: string; // Cognito user ID
}

const TOKEN_STORAGE_KEY = 'auth_tokens';
const USER_STORAGE_KEY = 'auth_user';

interface JwtPayload {
  email: string;
  sub: string;
  exp: number;
  iat: number;
  [key: string]: unknown;
}

/**
 * Parse JWT token to extract payload
 */
function parseJwt(token: string): JwtPayload | null {
  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );
    return JSON.parse(jsonPayload) as JwtPayload;
  } catch {
    return null;
  }
}

/**
 * Login with email and password
 */
export async function login(
  email: string,
  password: string
): Promise<{ success: boolean; requiresPasswordChange?: boolean; session?: string; error?: string }> {
  // Dev mode bypass
  if (isDevMode()) {
    return loginDev();
  }

  // Ensure config is loaded
  await loadRuntimeConfig();

  if (!COGNITO_CLIENT_ID) {
    return { success: false, error: 'Cognito configuration not available' };
  }

  try {
    const command = new InitiateAuthCommand({
      AuthFlow: AuthFlowType.USER_PASSWORD_AUTH,
      ClientId: COGNITO_CLIENT_ID,
      AuthParameters: {
        USERNAME: email,
        PASSWORD: password,
      },
    });

    const response = await getCognitoClient().send(command);

    // Check if password change is required (first login)
    if (response.ChallengeName === ChallengeNameType.NEW_PASSWORD_REQUIRED) {
      return {
        success: false,
        requiresPasswordChange: true,
        session: response.Session,
      };
    }

    // Successful authentication
    if (response.AuthenticationResult) {
      const tokens: AuthTokens = {
        idToken: response.AuthenticationResult.IdToken!,
        accessToken: response.AuthenticationResult.AccessToken!,
        refreshToken: response.AuthenticationResult.RefreshToken!,
        expiresAt: Date.now() + (response.AuthenticationResult.ExpiresIn! * 1000),
      };

      // Parse user info from ID token
      const payload = parseJwt(tokens.idToken);
      if (!payload) {
        return { success: false, error: 'Failed to parse authentication token' };
      }
      const user: AuthUser = {
        email: payload.email,
        sub: payload.sub,
      };

      // Store tokens and user
      localStorage.setItem(TOKEN_STORAGE_KEY, JSON.stringify(tokens));
      localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(user));

      return { success: true };
    }

    return { success: false, error: 'Authentication failed' };
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Login failed';
    return { success: false, error: message };
  }
}

/**
 * Change password after first login
 */
export async function changePasswordFirstLogin(
  email: string,
  _tempPassword: string,
  newPassword: string,
  session: string
): Promise<{ success: boolean; error?: string }> {
  // Ensure config is loaded
  await loadRuntimeConfig();

  if (!COGNITO_CLIENT_ID) {
    return { success: false, error: 'Cognito configuration not available' };
  }

  try {
    const command = new RespondToAuthChallengeCommand({
      ClientId: COGNITO_CLIENT_ID,
      ChallengeName: ChallengeNameType.NEW_PASSWORD_REQUIRED,
      Session: session,
      ChallengeResponses: {
        USERNAME: email,
        NEW_PASSWORD: newPassword,
      },
    });

    const response = await getCognitoClient().send(command);

    if (response.AuthenticationResult) {
      const tokens: AuthTokens = {
        idToken: response.AuthenticationResult.IdToken!,
        accessToken: response.AuthenticationResult.AccessToken!,
        refreshToken: response.AuthenticationResult.RefreshToken!,
        expiresAt: Date.now() + (response.AuthenticationResult.ExpiresIn! * 1000),
      };

      // Parse user info from ID token
      const payload = parseJwt(tokens.idToken);
      if (!payload) {
        return { success: false, error: 'Failed to parse authentication token' };
      }
      const user: AuthUser = {
        email: payload.email,
        sub: payload.sub,
      };

      // Store tokens and user
      localStorage.setItem(TOKEN_STORAGE_KEY, JSON.stringify(tokens));
      localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(user));

      return { success: true };
    }

    return { success: false, error: 'Password change failed' };
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'Password change failed';
    return { success: false, error: message };
  }
}

/**
 * Logout user
 */
export async function logout(): Promise<void> {
  // Dev mode bypass
  if (isDevMode()) {
    return logoutDev();
  }

  try {
    const tokens = getTokens();
    if (tokens) {
      // Global sign out from Cognito
      const command = new GlobalSignOutCommand({
        AccessToken: tokens.accessToken,
      });
      await getCognitoClient().send(command);
    }
  } catch {
    // Logout error - continue to clear local storage
  } finally {
    // Always clear local storage
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    localStorage.removeItem(USER_STORAGE_KEY);
  }
}

/**
 * Get stored tokens
 */
export function getTokens(): AuthTokens | null {
  // Dev mode bypass
  if (isDevMode()) {
    return getDevTokens();
  }

  try {
    const stored = localStorage.getItem(TOKEN_STORAGE_KEY);
    if (!stored) return null;

    const tokens: AuthTokens = JSON.parse(stored);

    // Check if tokens are expired
    if (tokens.expiresAt < Date.now()) {
      // Tokens expired, clear storage
      localStorage.removeItem(TOKEN_STORAGE_KEY);
      localStorage.removeItem(USER_STORAGE_KEY);
      return null;
    }

    return tokens;
  } catch {
    return null;
  }
}

/**
 * Get current user
 */
export function getCurrentUser(): AuthUser | null {
  // Dev mode bypass
  if (isDevMode()) {
    return getDevUser();
  }

  try {
    const stored = localStorage.getItem(USER_STORAGE_KEY);
    if (!stored) return null;
    return JSON.parse(stored);
  } catch {
    return null;
  }
}

/**
 * Check if user is authenticated
 */
export function isAuthenticated(): boolean {
  // Dev mode bypass
  if (isDevMode()) {
    initDevMode(); // Auto-login in dev mode
    return isAuthenticatedDev();
  }

  return getTokens() !== null;
}

/**
 * Get ID token for API calls
 */
export function getIdToken(): string | null {
  const tokens = getTokens();
  return tokens ? tokens.idToken : null;
}
