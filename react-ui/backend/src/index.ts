import express, { Request, Response } from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand, StopRuntimeSessionCommand } from '@aws-sdk/client-bedrock-agentcore';
import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3';
import { Readable } from 'stream';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { authenticateToken } from './middleware/auth';

dotenv.config();

const app = express();
const PORT = process.env.PORT || 3001;

// AWS Clients
// Extract region from AGENT_RUNTIME_ARN (e.g. arn:aws:bedrock-agentcore:us-east-1:...)
// so the client targets the correct region even when the frontend deploys elsewhere.
const agentRegion = process.env.AGENT_RUNTIME_ARN?.split(':')[3] || process.env.AWS_REGION || 'us-east-1';
const bedrockClient = new BedrockAgentCoreClient({
  region: agentRegion,
});

const s3Client = new S3Client({
  region: agentRegion,
});

// Middleware
app.use(cors({
  origin: process.env.FRONTEND_URL || 'http://localhost:5173',
  credentials: true
}));
app.use(express.json());

// Health check (public, no auth required)
app.get('/health', (req: Request, res: Response) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// Config endpoint (public, no auth required) - provides runtime config to frontend
app.get('/api/config', (req: Request, res: Response) => {
  res.json({
    cognito: {
      userPoolId: process.env.COGNITO_USER_POOL_ID,
      clientId: process.env.COGNITO_CLIENT_ID,
      region: process.env.COGNITO_REGION || 'us-east-1',
    },
  });
});

// Authentication middleware for all API routes
// In production: validates JWT tokens
// In development: bypasses validation
app.use('/api/*', authenticateToken);

// Helper function to sleep
function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Helper function to check if error is a throttling error
function isThrottlingError(error: any): boolean {
  const errorName = error.name || '';
  const statusCode = error.$metadata?.httpStatusCode;

  return (
    errorName === 'ThrottlingException' ||
    errorName === 'TooManyRequestsException' ||
    errorName === 'ServiceUnavailableException' ||
    statusCode === 429 ||
    statusCode === 503 ||
    (error.message && error.message.includes('Rate exceeded'))
  );
}

// Agent payload interface
interface AgentPayload {
  prompt: string;
  scenario_id?: string;
}

// Stream agent response using Server-Sent Events
app.post('/api/agent/invoke', async (req: Request, res: Response) => {
  const { prompt, sessionId, scenario_id } = req.body;

  if (!prompt || !sessionId) {
    return res.status(400).json({ error: 'prompt and sessionId are required' });
  }

  if (scenario_id) {
    console.log(`🔥 Scenario mode requested: ${scenario_id}`);
  }

  // Set headers for SSE
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');

  try {
    const agentRuntimeArn = process.env.AGENT_RUNTIME_ARN;
    if (!agentRuntimeArn) {
      throw new Error('AGENT_RUNTIME_ARN environment variable is not set');
    }

    console.log(`Invoking agent with session: ${sessionId}`);

    // Build agent payload with proper typing
    const payloadData: AgentPayload = { prompt: prompt };
    if (scenario_id) {
      payloadData.scenario_id = scenario_id;
    }

    const input = {
      runtimeSessionId: sessionId,  // Session ID (must be 33+ chars based on your example)
      agentRuntimeArn: agentRuntimeArn,  // Full ARN
      qualifier: 'DEFAULT',
      payload: new TextEncoder().encode(JSON.stringify(payloadData)),
    };

    const command = new InvokeAgentRuntimeCommand(input);

    // Retry logic for rate limiting
    const maxRetries = 3;
    let response;
    let lastError;

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        response = await bedrockClient.send(command);

        if (attempt > 1) {
          console.log(`✅ Agent invocation succeeded on attempt ${attempt}/${maxRetries}`);
          // Notify frontend about successful retry
          res.write(`data: ${JSON.stringify({ type: 'chunk', content: `\n[Retry successful after ${attempt} attempts]\n` })}\n\n`);
        }

        break; // Success - exit retry loop
      } catch (error) {
        lastError = error;

        if (isThrottlingError(error) && attempt < maxRetries) {
          console.warn(`⏳ Rate limit hit on attempt ${attempt}/${maxRetries}. Waiting 5 seconds before retry...`);
          // Notify frontend about retry
          res.write(`data: ${JSON.stringify({ type: 'chunk', content: `\n[Rate limit hit, retrying in 5 seconds... (attempt ${attempt}/${maxRetries})]\n` })}\n\n`);
          await sleep(5000);
          continue; // Retry
        } else {
          // Non-throttling error or max retries exceeded
          throw error;
        }
      }
    }

    if (!response) {
      throw lastError || new Error('Failed to invoke agent after retries');
    }

    // Stream the response - mimicking Python's iter_lines() behavior
    if (response.response) {
      const stream = response.response as any;

      // The response is an SSE stream from Bedrock, similar to Python version
      let buffer = '';

      try {
        for await (const chunk of stream) {
          if (chunk) {
            // Decode the chunk
            const chunkText = typeof chunk === 'string' ? chunk : new TextDecoder().decode(chunk);
            buffer += chunkText;

            // Process complete lines (SSE format: "data: ...")
            const lines = buffer.split('\n');
            buffer = lines.pop() || ''; // Keep incomplete line in buffer

            for (const line of lines) {
              if (line.startsWith('data: ')) {
                const data = line.substring(6).trim();
                if (data && data !== '[DONE]') {
                  // Remove outer quotes if present (SSE wraps data in quotes)
                  let cleaned = data;
                  if (cleaned.startsWith('"') && cleaned.endsWith('"')) {
                    cleaned = cleaned.slice(1, -1);
                    // After removing outer quotes, unescape the content
                    // This converts {\"toolUseId\": ...} back to {"toolUseId": ...}
                    cleaned = cleaned.replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\\\/g, '\\');
                  }

                  if (cleaned) {
                    // Send to frontend
                    res.write(`data: ${JSON.stringify({ type: 'chunk', content: cleaned })}\n\n`);
                  }
                }
              }
            }
          }
        }
        console.log('✅ Bedrock stream completed successfully');
      } catch (streamError) {
        console.error('❌ Error reading from Bedrock stream:', streamError);
        throw streamError;
      }

      // Process any remaining data in buffer
      if (buffer.trim()) {
        if (buffer.startsWith('data: ')) {
          const data = buffer.substring(6).trim();
          if (data && data !== '[DONE]') {
            // Remove outer quotes if present (SSE wraps data in quotes)
            let cleaned = data;
            if (cleaned.startsWith('"') && cleaned.endsWith('"')) {
              cleaned = cleaned.slice(1, -1);
              // After removing outer quotes, unescape the content
              cleaned = cleaned.replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\\\/g, '\\');
            }
            if (cleaned) {
              res.write(`data: ${JSON.stringify({ type: 'chunk', content: cleaned })}\n\n`);
            }
          }
        }
      }
    }

    // Send completion event
    console.log('📡 Sending done event to frontend');
    res.write(`data: ${JSON.stringify({ type: 'done' })}\n\n`);
    
    // Give the client time to receive and process the done event
    // Then close the connection gracefully
    await sleep(500);
    
    console.log('📡 Closing SSE connection');
    res.end();

  } catch (error) {
    console.error('Error invoking agent:', error);
    const errorMessage = error instanceof Error ? error.message : String(error);
    
    // Only write if response is still writable
    if (!res.writableEnded) {
      res.write(`data: ${JSON.stringify({ type: 'error', message: errorMessage })}\n\n`);
      res.end();
    }
  }
});

// Stop agent runtime session
app.post('/api/agent/stop-session', async (req: Request, res: Response) => {
  const { sessionId } = req.body;

  if (!sessionId) {
    return res.status(400).json({ error: 'sessionId is required' });
  }

  try {
    const agentRuntimeArn = process.env.AGENT_RUNTIME_ARN;
    if (!agentRuntimeArn) {
      throw new Error('AGENT_RUNTIME_ARN environment variable is not set');
    }

    console.log(`Stopping runtime session: ${sessionId}`);

    const command = new StopRuntimeSessionCommand({
      runtimeSessionId: sessionId,
      agentRuntimeArn: agentRuntimeArn,
      qualifier: 'DEFAULT',
    });

    await bedrockClient.send(command);

    console.log(`✅ Successfully stopped session: ${sessionId}`);
    res.json({ success: true, message: 'Session stopped successfully' });

  } catch (error) {
    console.error('Error stopping session:', error);
    const errorMessage = error instanceof Error ? error.message : String(error);
    res.status(500).json({ error: 'Failed to stop session', message: errorMessage });
  }
});

// Generate pre-signed URL for private S3 objects
app.get('/api/presigned-url', async (req: Request, res: Response) => {
  const { s3Url } = req.query;

  if (!s3Url || typeof s3Url !== 'string') {
    return res.status(400).json({ error: 's3Url parameter is required' });
  }

  console.log(`Generating pre-signed URL for: ${s3Url}`);

  try {
    // Parse S3 URL to extract bucket and key
    const s3Match = s3Url.match(/^s3:\/\/([^\/]+)\/(.+)$/);
    if (!s3Match) {
      return res.status(400).json({ error: 'Invalid S3 URL format. Expected: s3://bucket/key' });
    }

    const bucket = s3Match[1];
    const key = s3Match[2];

    // Generate pre-signed URL (valid for 1 hour)
    const { getSignedUrl } = await import('@aws-sdk/s3-request-presigner');
    const command = new GetObjectCommand({
      Bucket: bucket,
      Key: key,
    });

    const presignedUrl = await getSignedUrl(s3Client, command, { expiresIn: 3600 });

    console.log(`Generated pre-signed URL (expires in 1 hour)`);

    res.json({ presignedUrl });

  } catch (error) {
    console.error('Error generating pre-signed URL:', error);
    const errorMessage = error instanceof Error ? error.message : String(error);
    res.status(500).json({ error: 'Failed to generate pre-signed URL', message: errorMessage });
  }
});

// Fetch scenario configuration for frontend pre-loading
app.get('/api/scenario/:scenarioId', async (req: Request, res: Response) => {
  const { scenarioId } = req.params;

  if (!scenarioId) {
    return res.status(400).json({ error: 'scenarioId parameter is required' });
  }

  // Validate scenarioId to prevent path traversal attacks
  // Only allow lowercase letters, numbers, and hyphens
  const scenarioIdPattern = /^[a-z0-9-]+$/;
  if (!scenarioIdPattern.test(scenarioId)) {
    return res.status(400).json({
      error: 'Invalid scenarioId format',
      message: 'scenarioId must contain only lowercase letters, numbers, and hyphens'
    });
  }

  console.log(`Fetching scenario config for: ${scenarioId}`);

  try {
    const bucket = process.env.S3_BUCKET_NAME;
    if (!bucket) {
      throw new Error('S3_BUCKET_NAME environment variable is not configured');
    }

    const scenarioPrefix = `use-cases/${scenarioId}/`;

    // Fetch config.json
    const configKey = `${scenarioPrefix}config.json`;
    console.log(`Fetching config from: s3://${bucket}/${configKey}`);

    const configCommand = new GetObjectCommand({
      Bucket: bucket,
      Key: configKey,
    });

    const configResponse = await s3Client.send(configCommand);
    if (!configResponse.Body) {
      return res.status(404).json({ error: 'Scenario not found' });
    }

    // Parse config
    const configChunks: Uint8Array[] = [];
    const configStream = configResponse.Body as Readable;
    for await (const chunk of configStream) {
      configChunks.push(chunk);
    }
    const configBuffer = Buffer.concat(configChunks);
    const config = JSON.parse(configBuffer.toString('utf-8'));
    console.log(`📋 Config loaded from S3:`, JSON.stringify(config, null, 2));

    // Fetch narrative.md (optional)
    let narrative = '';
    try {
      const narrativeKey = `${scenarioPrefix}narrative.md`;
      const narrativeCommand = new GetObjectCommand({
        Bucket: bucket,
        Key: narrativeKey,
      });
      const narrativeResponse = await s3Client.send(narrativeCommand);
      if (narrativeResponse.Body) {
        const narrativeChunks: Uint8Array[] = [];
        const narrativeStream = narrativeResponse.Body as Readable;
        for await (const chunk of narrativeStream) {
          narrativeChunks.push(chunk);
        }
        narrative = Buffer.concat(narrativeChunks).toString('utf-8');
      }
    } catch (e) {
      console.log(`No narrative.md found for ${scenarioId}`);
    }

    // Build asset URLs with dates from config
    const s3Prefix = `s3://${bucket}/${scenarioPrefix}`;
    const beforeDate = config.dates?.before;
    const afterDate = config.dates?.after;

    const scenario = {
      id: scenarioId,
      name: config.name || scenarioId,
      description: config.description || '',
      location: config.location || '',
      user_question: config.user_question || 'Analyze this area',
      index_type: config.index_type,
      dates: config.dates || {},
      narrative: narrative,
      tool_calls: config.tool_calls || [],
      assets: {
        geometry_url: `${s3Prefix}geometry.geojson`,
        before: {
          tci: beforeDate ? `${s3Prefix}before/tci-${beforeDate}.tif` : `${s3Prefix}before/tci.tif`,
          red: beforeDate ? `${s3Prefix}before/red-${beforeDate}.tif` : `${s3Prefix}before/red.tif`,
          nir08: beforeDate ? `${s3Prefix}before/nir08-${beforeDate}.tif` : `${s3Prefix}before/nir08.tif`,
          swir2: beforeDate ? `${s3Prefix}before/swir2-${beforeDate}.tif` : `${s3Prefix}before/swir2.tif`,
          nbr: beforeDate ? `${s3Prefix}before/nbr-${beforeDate}.tif` : `${s3Prefix}before/nbr.tif`,
          ndvi: beforeDate ? `${s3Prefix}before/ndvi-${beforeDate}.tif` : `${s3Prefix}before/ndvi.tif`,
          ndwi: beforeDate ? `${s3Prefix}before/ndwi-${beforeDate}.tif` : `${s3Prefix}before/ndwi.tif`,
        },
        after: {
          tci: afterDate ? `${s3Prefix}after/tci-${afterDate}.tif` : `${s3Prefix}after/tci.tif`,
          red: afterDate ? `${s3Prefix}after/red-${afterDate}.tif` : `${s3Prefix}after/red.tif`,
          nir08: afterDate ? `${s3Prefix}after/nir08-${afterDate}.tif` : `${s3Prefix}after/nir08.tif`,
          swir2: afterDate ? `${s3Prefix}after/swir2-${afterDate}.tif` : `${s3Prefix}after/swir2.tif`,
          nbr: afterDate ? `${s3Prefix}after/nbr-${afterDate}.tif` : `${s3Prefix}after/nbr.tif`,
          ndvi: afterDate ? `${s3Prefix}after/ndvi-${afterDate}.tif` : `${s3Prefix}after/ndvi.tif`,
          ndwi: afterDate ? `${s3Prefix}after/ndwi-${afterDate}.tif` : `${s3Prefix}after/ndwi.tif`,
        },
      },
    };

    console.log(`Successfully loaded scenario: ${scenario.name}`);
    console.log(`Index type: ${scenario.index_type}`);
    console.log(`NDVI URLs: before=${scenario.assets.before.ndvi}, after=${scenario.assets.after.ndvi}`);
    res.json(scenario);

  } catch (error) {
    console.error('Error loading scenario:', error);
    const errorMessage = error instanceof Error ? error.message : String(error);
    res.status(500).json({ error: 'Failed to load scenario', message: errorMessage });
  }
});

// Load geometry from S3 GeoJSON file
app.get('/api/geometry', async (req: Request, res: Response) => {
  const { s3Url } = req.query;

  if (!s3Url || typeof s3Url !== 'string') {
    return res.status(400).json({ error: 's3Url parameter is required' });
  }

  console.log(`Loading geometry from: ${s3Url}`);

  try {
    // Parse S3 URL to extract bucket and key
    // Format: s3://bucket-name/path/to/file.geojson
    const s3Match = s3Url.match(/^s3:\/\/([^\/]+)\/(.+)$/);
    if (!s3Match) {
      return res.status(400).json({ error: 'Invalid S3 URL format. Expected: s3://bucket/key' });
    }

    const bucket = s3Match[1];
    const key = s3Match[2];

    console.log(`Fetching from bucket: ${bucket}, key: ${key}`);

    // Fetch from S3
    const command = new GetObjectCommand({
      Bucket: bucket,
      Key: key,
    });

    const response = await s3Client.send(command);

    if (!response.Body) {
      return res.status(404).json({ error: 'File not found in S3' });
    }

    // Read the stream and parse as JSON
    const chunks: Uint8Array[] = [];
    const stream = response.Body as Readable;

    for await (const chunk of stream) {
      chunks.push(chunk);
    }

    const buffer = Buffer.concat(chunks);
    const geojsonText = buffer.toString('utf-8');
    const geojson = JSON.parse(geojsonText);

    console.log(`Successfully loaded GeoJSON with ${geojson.features?.length || 0} features`);

    // Return the GeoJSON
    res.json(geojson);

  } catch (error) {
    console.error('Error loading geometry from S3:', error);
    const errorMessage = error instanceof Error ? error.message : String(error);
    res.status(500).json({ error: 'Failed to load geometry', message: errorMessage });
  }
});

// Serve static frontend files in production (MUST be last - after all API routes)
if (process.env.NODE_ENV === 'production') {
  const frontendPath = path.join(__dirname, '..', 'public');
  console.log(`Serving static frontend from: ${frontendPath}`);

  app.use(express.static(frontendPath));

  // Handle React Router - send all non-API requests to index.html
  app.get('*', (req: Request, res: Response) => {
    res.sendFile(path.join(frontendPath, 'index.html'));
  });
}

// Start server
app.listen(PORT, () => {
  console.log(`Backend server running on http://localhost:${PORT}`);
  console.log(`CORS enabled for: ${process.env.FRONTEND_URL || 'http://localhost:5173'}`);
  if (process.env.NODE_ENV === 'production') {
    console.log(`Frontend static files enabled`);
  }
});
