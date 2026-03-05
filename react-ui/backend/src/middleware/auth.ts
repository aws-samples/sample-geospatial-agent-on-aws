/**
 * JWT Authentication Middleware
 *
 * Validates Cognito JWT tokens on all API requests
 * Bypasses validation in development mode (NODE_ENV !== 'production')
 */

import { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';
import jwksClient from 'jwks-rsa';

const COGNITO_USER_POOL_ID = process.env.COGNITO_USER_POOL_ID;
const COGNITO_REGION = process.env.COGNITO_REGION || 'us-east-1';
const IS_PRODUCTION = process.env.NODE_ENV === 'production';

// Cognito JWKS URL
const JWKS_URI = `https://cognito-idp.${COGNITO_REGION}.amazonaws.com/${COGNITO_USER_POOL_ID}/.well-known/jwks.json`;

// JWKS client for fetching Cognito public keys
const client = jwksClient({
  jwksUri: JWKS_URI,
  cache: true,
  cacheMaxAge: 3600000, // 1 hour
});

/**
 * Get signing key from Cognito JWKS
 */
function getKey(header: any, callback: any) {
  client.getSigningKey(header.kid, (err, key) => {
    if (err) {
      callback(err);
      return;
    }
    const signingKey = key?.getPublicKey();
    callback(null, signingKey);
  });
}

/**
 * Verify JWT token
 */
function verifyToken(token: string): Promise<any> {
  return new Promise((resolve, reject) => {
    jwt.verify(
      token,
      getKey,
      {
        algorithms: ['RS256'],
        issuer: `https://cognito-idp.${COGNITO_REGION}.amazonaws.com/${COGNITO_USER_POOL_ID}`,
      },
      (err, decoded) => {
        if (err) {
          reject(err);
        } else {
          resolve(decoded);
        }
      }
    );
  });
}

/**
 * Authentication middleware
 *
 * In production: Validates JWT tokens from Authorization header
 * In development: Bypasses validation (allows any request)
 */
export async function authenticateToken(
  req: Request,
  res: Response,
  next: NextFunction
): Promise<void> {
  // Bypass auth in development mode
  if (!IS_PRODUCTION) {
    console.log('🔧 DEV MODE: Bypassing authentication');
    next();
    return;
  }

  // Get token from Authorization header
  const authHeader = req.headers.authorization;
  const token = authHeader && authHeader.split(' ')[1]; // Bearer TOKEN

  if (!token) {
    res.status(401).json({
      error: 'Unauthorized',
      message: 'Missing authentication token',
    });
    return;
  }

  try {
    // Verify token
    const decoded = await verifyToken(token);

    // Attach user info to request
    (req as any).user = {
      sub: decoded.sub,
      email: decoded.email,
      username: decoded['cognito:username'],
    };

    console.log(`✅ Authenticated user: ${decoded.email}`);
    next();
  } catch (error) {
    console.error('❌ Token verification failed:', error);
    res.status(403).json({
      error: 'Forbidden',
      message: 'Invalid or expired token',
    });
  }
}

/**
 * Optional: Extract user from request
 */
export function getAuthenticatedUser(req: Request): any {
  return (req as any).user;
}
