import type { GeometryData, StreamEvent } from '../types.ts';
import { getIdToken } from '../utils/auth';

// API URL: In production (when served from same origin), use current origin
// In development, use localhost:3001 or VITE_API_URL from env
const getApiUrl = () => {
  // If explicitly set in env, use it
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL;
  }

  // If in dev mode or on localhost, use localhost:3001
  if (import.meta.env.VITE_DEV_MODE === 'true' || window.location.hostname === 'localhost') {
    return 'http://localhost:3001';
  }

  // Production: use same origin (CloudFront/ALB serves both frontend + backend)
  return window.location.origin;
};

const API_URL = getApiUrl();

/**
 * Get authentication headers for API requests
 * Includes Authorization header with ID token if available
 */
function getAuthHeaders(): HeadersInit {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  };

  // Add Authorization header if user is authenticated
  const idToken = getIdToken();
  if (idToken) {
    headers['Authorization'] = `Bearer ${idToken}`;
  }

  return headers;
}

interface AgentInvokePayload {
  prompt: string;
  sessionId: string;
  scenario_id?: string;
}

/**
 * Stream agent responses using Server-Sent Events
 */
export async function* streamAgentInvoke(
  prompt: string,
  sessionId: string,
  scenarioId?: string
): AsyncGenerator<StreamEvent> {
  const payload: AgentInvokePayload = { prompt, sessionId };
  if (scenarioId) {
    payload.scenario_id = scenarioId;
    console.log('🔥 Sending request with scenario_id:', scenarioId);
  }
  console.log('📤 Agent payload:', payload);

  const response = await fetch(`${API_URL}/api/agent/invoke`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error('No response body');
  }

  const decoder = new TextDecoder();
  let buffer = '';
  let receivedDoneEvent = false;

  try {
    while (true) {
      const { done, value } = await reader.read();

      if (done) {
        console.log('📡 Stream reader done');
        break;
      }

      buffer += decoder.decode(value, { stream: true });

      // Process complete SSE messages
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // Keep incomplete line in buffer

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data.trim()) {
            try {
              const event: StreamEvent = JSON.parse(data);
              // When we receive 'done' event, mark it and exit the read loop
              if (event.type === 'done') {
                console.log('📡 Received done event from backend - exiting read loop');
                receivedDoneEvent = true;
                // Break out of both loops
                return;
              }
              yield event;
            } catch (e) {
              console.error('Failed to parse SSE data:', e);
            }
          }
        }
      }
    }
  } catch (error) {
    // If we received a 'done' event and then got a network error, treat it as normal completion
    if (receivedDoneEvent && error instanceof Error && error.message === 'network error') {
      console.log('📡 Network error after done event - treating as normal completion');
      return;
    }
    
    // Check if this is a network error that might be due to connection closing
    const isNetworkError = error instanceof Error && error.message === 'network error';
    const isTypeError = error instanceof TypeError;
    
    if (isNetworkError || isTypeError) {
      console.warn('⚠️ Network/Type error during stream read - this may be due to connection closing');
      console.warn('⚠️ Received done event:', receivedDoneEvent);
      console.warn('⚠️ Buffer remaining:', buffer.length, 'bytes');
      
      // If we got a network error but haven't received done event, 
      // and we have some content, treat as partial success
      if (!receivedDoneEvent && buffer.length === 0) {
        console.log('📡 Network error with no remaining buffer - treating as normal completion');
        return;
      }
    }
    
    // Only throw error if it's not a connection closing issue
    if (!isNetworkError && !isTypeError) {
      console.error('❌ Stream read error:', error);
      console.error('❌ Error type:', error instanceof Error ? error.constructor.name : typeof error);
      console.error('❌ Error message:', error instanceof Error ? error.message : String(error));
      throw error;
    } else {
      // Log but don't throw for network/type errors (connection closing)
      console.warn('⚠️ Stream connection closed, treating as completion');
      return;
    }
  } finally {
    try {
      reader.releaseLock();
      console.log('📡 Reader lock released');
    } catch (e) {
      console.warn('⚠️ Error releasing reader lock:', e);
    }
  }
  
  console.log('✅ Stream completed successfully');
}

/**
 * Get pre-signed URL for private S3 object
 */
export async function getPresignedUrl(s3Url: string): Promise<string | null> {
  try {
    const response = await fetch(
      `${API_URL}/api/presigned-url?s3Url=${encodeURIComponent(s3Url)}`,
      { headers: getAuthHeaders() }
    );

    if (!response.ok) {
      console.error(`❌ Pre-signed URL failed (${response.status}) for:`, s3Url);
      const errorData = await response.json().catch(() => null);
      if (errorData) console.error('Error:', errorData);
      return null;
    }

    const data = await response.json();
    return data.presignedUrl;
  } catch (error) {
    console.error('❌ Pre-signed URL error:', error);
    return null;
  }
}

/**
 * Load geometry from S3 via backend proxy
 */
export async function loadGeometry(s3Url: string): Promise<GeometryData | null> {
  try {
    const response = await fetch(
      `${API_URL}/api/geometry?s3Url=${encodeURIComponent(s3Url)}`,
      { headers: getAuthHeaders() }
    );

    if (!response.ok) {
      console.error('Failed to load geometry:', response.statusText);
      return null;
    }

    const data = await response.json();
    return data as GeometryData;
  } catch (error) {
    console.error('Error loading geometry:', error);
    return null;
  }
}

/**
 * Stop an active AgentCore runtime session
 */
export async function stopRuntimeSession(sessionId: string): Promise<boolean> {
  try {
    const response = await fetch(`${API_URL}/api/agent/stop-session`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ sessionId }),
    });

    if (!response.ok) {
      console.error('Failed to stop session:', response.statusText);
      return false;
    }

    const data = await response.json();
    console.log('✅ Session stopped:', data.message);
    return true;
  } catch (error) {
    console.error('Error stopping session:', error);
    return false;
  }
}
