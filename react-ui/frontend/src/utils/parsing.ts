import type { ToolCall } from '../types.ts';

/**
 * Clean streaming text from formatting artifacts
 * Keep it minimal to preserve text structure
 */
export function cleanStreamingText(text: string): string {
  if (!text) {
    return '';
  }

  // Only decode HTML entities - don't mess with quotes or structure
  let cleaned = text
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>');

  return cleaned;
}

/**
 * Parse parameters from various formats (JSON or Python-dict style)
 */
export function parseParams(paramsStr: string): Record<string, any> {
  const paramsDict: Record<string, any> = {};

  if (!paramsStr) {
    return paramsDict;
  }

  // Remove outer braces and whitespace
  let content = paramsStr.trim();
  if (content.startsWith('{')) {
    content = content.substring(1);
  }
  if (content.endsWith('}')) {
    content = content.substring(0, content.length - 1);
  }
  content = content.trim();

  if (!content) {
    return paramsDict;
  }

  // Try JSON parsing first
  try {
    return JSON.parse('{' + content + '}');
  } catch (e) {
    // Manual parsing for Python-dict style format
  }

  // Find all key positions - require space after colon OR beginning of string/comma before key
  const keyPattern = /(?:^|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s+(?!\/\/)/g;
  const matches = Array.from(content.matchAll(keyPattern));

  for (let i = 0; i < matches.length; i++) {
    const match = matches[i];
    const key = match[1].trim();
    const valueStart = match.index! + match[0].length;

    // Value goes until the next key or end of string
    const valueEnd = i + 1 < matches.length ? matches[i + 1].index! : content.length;

    let value = content.substring(valueStart, valueEnd).trim();

    // Remove trailing comma if present
    if (value.endsWith(',')) {
      value = value.substring(0, value.length - 1).trim();
    }

    paramsDict[key] = value;
  }

  return paramsDict;
}

/**
 * Parse JSON-format tool calls (new format)
 * Format: {"toolUseId": "...", "name": "...", "input": "{\"param\": \"value\"}"}
 */
function parseJsonToolCalls(text: string): ToolCall[] {
  const tools: ToolCall[] = [];
  const seenIds = new Set<string>();

  // Find all JSON objects that look like tool calls
  // Simple approach: find positions of {"toolUseId": and match balanced braces
  let pos = 0;
  while (pos < text.length) {
    const start = text.indexOf('{"toolUseId":', pos);
    if (start === -1) break;

    // Find the matching closing brace
    let braceCount = 0;
    let end = start;
    let inString = false;
    let escapeNext = false;

    for (let i = start; i < text.length; i++) {
      const char = text[i];

      if (escapeNext) {
        escapeNext = false;
        continue;
      }

      if (char === '\\') {
        escapeNext = true;
        continue;
      }

      if (char === '"') {
        inString = !inString;
        continue;
      }

      if (!inString) {
        if (char === '{') braceCount++;
        if (char === '}') {
          braceCount--;
          if (braceCount === 0) {
            end = i + 1;
            break;
          }
        }
      }
    }

    if (braceCount === 0 && end > start) {
      // Found a complete JSON object
      const jsonStr = text.substring(start, end);

      try {
        const toolObj = JSON.parse(jsonStr);

        // Validate it has the required fields
        if (toolObj.toolUseId && toolObj.name && toolObj.input !== undefined) {
          const toolId = toolObj.toolUseId;

          // Skip duplicates
          if (!seenIds.has(toolId)) {
            seenIds.add(toolId);

            // Parse the input field (which is a JSON string)
            let params = {};
            try {
              params = JSON.parse(toolObj.input);
            } catch (e) {
              console.warn(`⚠️ Failed to parse input for ${toolObj.name}:`, toolObj.input);
              // Fallback: try to extract key fields manually
              const s3Match = toolObj.input.match(/s3_url["\s:]+([^"]+)/);
              const titleMatch = toolObj.input.match(/title["\s:]+([^"]+)/);
              if (s3Match) params = { ...params, s3_url: s3Match[1] };
              if (titleMatch) params = { ...params, title: titleMatch[1] };
            }

            tools.push({
              name: toolObj.name,
              id: toolId,
              params: params,
              status: 'completed',
            });
          }
        }
      } catch (e) {
        console.warn(`⚠️ Failed to parse JSON at position ${start}:`, jsonStr.substring(0, 100), e);
      }

      pos = end;
    } else {
      pos = start + 1;
    }
  }

  return tools;
}

/**
 * Parse tool calls and results from streaming response
 * Now using JSON format only
 */
export function parseToolCalls(text: string): { cleanText: string; tools: ToolCall[] } {
  // Parse JSON format tool calls
  const jsonTools = parseJsonToolCalls(text);

  // Remove JSON tool calls from text using the same brace-matching logic
  let cleanText = text;
  let pos = 0;
  const toRemove: Array<{ start: number; end: number }> = [];

  while (pos < cleanText.length) {
    const start = cleanText.indexOf('{"toolUseId":', pos);
    if (start === -1) break;

    // Find the matching closing brace
    let braceCount = 0;
    let end = start;
    let inString = false;
    let escapeNext = false;

    for (let i = start; i < cleanText.length; i++) {
      const char = cleanText[i];

      if (escapeNext) {
        escapeNext = false;
        continue;
      }

      if (char === '\\') {
        escapeNext = true;
        continue;
      }

      if (char === '"') {
        inString = !inString;
        continue;
      }

      if (!inString) {
        if (char === '{') braceCount++;
        if (char === '}') {
          braceCount--;
          if (braceCount === 0) {
            end = i + 1;
            break;
          }
        }
      }
    }

    if (braceCount === 0 && end > start) {
      toRemove.push({ start, end });
      pos = end;
    } else {
      pos = start + 1;
    }
  }

  // Remove in reverse order to preserve indices
  for (let i = toRemove.length - 1; i >= 0; i--) {
    const { start, end } = toRemove[i];
    cleanText = cleanText.substring(0, start) + cleanText.substring(end);
  }

  // Also remove any INCOMPLETE JSON objects (streaming but not yet closed)
  // These start with {"toolUseId": but don't have a matching closing brace
  const incompleteStart = cleanText.indexOf('{"toolUseId":');
  if (incompleteStart !== -1) {
    // Found an incomplete JSON, remove everything from this point onwards
    cleanText = cleanText.substring(0, incompleteStart);
  }

  // Fix markdown spacing: ensure ## headings are on their own line
  cleanText = cleanText.replace(/([^\n])(##\s)/g, '$1\n\n$2');

  return { cleanText: cleanText.trim(), tools: jsonTools };

  /* ===== OLD XML FORMAT PARSING (DISABLED) =====
   * This was the previous format with incremental/repeated chunks
   * Format: <tool_call>\nname: display_visual\nid: tooluse_...\nparams: {...}\n</tool_call>
   *
   * Keeping this code commented out in case we need to revert:
   *
   * const toolCallPattern = /<tool_call>(.*?)<\/tool_call>/gs;
   * const toolResultPattern = /<tool_result>(.*?)<\/tool_result>/gs;
   * const toolCallMatches = Array.from(text.matchAll(toolCallPattern));
   * const toolResultMatches = Array.from(text.matchAll(toolResultPattern));
   *
   * const parsedTools: ToolCall[] = [];
   * const seenTools = new Set<string>();
   *
   * // Parse tool results and extract from tool calls...
   * // (full code preserved in git history)
   *
   * return { cleanText, tools: parsedTools };
   */
}

/**
 * Parse tools progressively as they appear in stream
 * Now using JSON format only
 */
export function parseProgressiveTools(text: string, existingTools: ToolCall[]): ToolCall[] {
  // Parse JSON format tool calls
  const jsonTools = parseJsonToolCalls(text);

  // Merge with existing tools, avoiding duplicates
  const existingIds = new Set(existingTools.map(t => t.id));
  const newTools = jsonTools.filter(t => !existingIds.has(t.id));
  return [...existingTools, ...newTools];

  /* ===== OLD XML FORMAT PROGRESSIVE PARSING (DISABLED) =====
   * This was for handling incremental/repeated tool call chunks
   *
   * Keeping note that full code is preserved in git history.
   * The old XML format required complex handling for:
   * - Incremental params building
   * - Temp ID to real ID upgrades
   * - Tool result matching
   *
   * New JSON format is much simpler and emits complete tools once.
   */
}

/**
 * Extract geometry URL and location name from tool calls
 */
export function extractGeometryUrl(tools: ToolCall[]): string | null {
  for (const tool of tools) {
    if (tool.name === 'get_rasters' || tool.name === 'get_ndvi_stats') {
      const geometryUrl = tool.params.geometry_s3_url;
      if (geometryUrl) {
        return geometryUrl;
      }
    }
  }
  return null;
}

/**
 * Extract geometry URL and location name from display_visual tool calls
 * Only parses .geojson files (geometries)
 */
export function extractGeometryData(tools: ToolCall[]): { url: string; location: string } | null {
  for (const tool of tools) {
    // Only look at display_visual tools
    if (tool.name !== 'display_visual') {
      continue;
    }
    
    const s3Url = tool.params.s3_url;
    const title = tool.params.title;
    
    // Only process .geojson files (geometries)
    if (s3Url && title && s3Url.toLowerCase().endsWith('.geojson')) {
      return { url: s3Url, location: title };
    }
  }
  return null;
}

/**
 * Check if a display_visual tool call has complete parameters
 * Similar to the completeness check in the Streamlit version
 */
function isDisplayVisualComplete(tool: ToolCall): boolean {
  // Must have the tool name
  if (tool.name !== 'display_visual') {
    return false;
  }

  // Must have params object
  if (!tool.params || typeof tool.params !== 'object') {
    return false;
  }

  // Must have s3_url parameter (required)
  if (!tool.params.s3_url || typeof tool.params.s3_url !== 'string') {
    return false;
  }

  // Must have title parameter (required)
  if (!tool.params.title || typeof tool.params.title !== 'string') {
    return false;
  }

  // s3_url must be a valid S3 URL format
  if (!tool.params.s3_url.startsWith('s3://')) {
    return false;
  }

  // All required params present and valid
  return true;
}

/**
 * Extract ALL visualization data from display_visual tool calls
 * Returns both rasters (.tif) and geometries (.geojson)
 */
export function extractAllVisualizationData(tools: ToolCall[]): {
  rasters: Array<{ url: string; title: string; date?: string; cloudCoverage?: string }>;
  geometries: Array<{ url: string; title: string }>;
} {
  const rasters: Array<{ url: string; title: string; date?: string; cloudCoverage?: string }> = [];
  const geometries: Array<{ url: string; title: string }> = [];
  const seenUrls = new Set<string>();

  for (const tool of tools) {
    // Only process display_visual tools
    if (tool.name !== 'display_visual') {
      continue;
    }

    // Check if tool call is complete (has all required params)
    if (!isDisplayVisualComplete(tool)) {
      continue;
    }

    const s3Url = tool.params.s3_url;
    const title = tool.params.title;
    const description = tool.params.description || '';

    // Skip if we've already seen this S3 URL
    if (seenUrls.has(s3Url)) {
      continue;
    }
    
    seenUrls.add(s3Url);
    
    // Determine type by file extension
    if (s3Url.toLowerCase().endsWith('.geojson')) {
      // Geometry file
      geometries.push({
        url: s3Url,
        title: title
      });
      continue;
    }
    
    if (s3Url.toLowerCase().endsWith('.tif')) {
      // Raster file - extract date and cloud coverage
      let date: string | undefined;

      // Always extract date from S3 URL filename (e.g., ndvi_clipped_geneva_2025-10-07.tif)
      const s3DateMatch = s3Url.match(/(\d{4}-\d{2}-\d{2})/);
      if (s3DateMatch) {
        date = s3DateMatch[1];
      }

      // Extract cloud coverage from description
      let cloudCoverage: string | undefined;
      const cloudMatch = description.match(/\(?([\d.]+)%\s*cloud\s*coverage\)?/i);
      if (cloudMatch) {
        cloudCoverage = cloudMatch[1] + '%';
      }

      rasters.push({
        url: s3Url,
        title: title,
        date: date,
        cloudCoverage: cloudCoverage
      });
    }
  }
  
  return { rasters, geometries };
}

/**
 * Legacy function - use extractAllVisualizationData instead
 */
export function extractAllRasterData(tools: ToolCall[]): Array<{ url: string; title: string; date?: string; cloudCoverage?: string }> {
  const { rasters } = extractAllVisualizationData(tools);
  return rasters;
}

/**
 * Extract raster S3 URL and title from tool calls (DEPRECATED - use extractAllRasterData)
 * Returns the first raster found from display_visual tools
 */
export function extractRasterData(tools: ToolCall[]): { url: string; title: string } | null {
  const allRasters = extractAllRasterData(tools);
  return allRasters.length > 0 ? allRasters[0] : null;
}

/**
 * Extract location_changed flag from LLM response
 *
 * TODO: This is a placeholder function for future implementation
 * When the LLM starts providing location_changed information in responses,
 * implement this function to parse it from the response text or tool calls.
 *
 * Expected formats could be:
 * - A special tag in response: <location_changed>true</location_changed>
 * - A tool call parameter: { location_changed: true }
 * - A metadata field in the stream event
 *
 * @param responseText - The full response text from LLM
 * @param tools - Array of tool calls from the response
 * @returns boolean indicating if location has changed
 */
export function extractLocationChanged(_responseText: string, _tools: ToolCall[]): boolean {
  // TODO: Implement parsing logic when LLM provides this information
  // With persistent layer management, we don't auto-clear anymore
  // Users can manually clear using the "Clear All" button

  // Example implementation patterns for future:
  // 1. Parse from XML-style tag:
  //    const match = _responseText.match(/<location_changed>(true|false)<\/location_changed>/);
  //    if (match) return match[1] === 'true';

  // 2. Parse from tool call metadata:
  //    const metaTool = _tools.find(t => t.name === 'metadata');
  //    if (metaTool?.params.location_changed !== undefined) {
  //      return metaTool.params.location_changed;
  //    }

  return false; // Default: don't clear (persistent layer management)
}
