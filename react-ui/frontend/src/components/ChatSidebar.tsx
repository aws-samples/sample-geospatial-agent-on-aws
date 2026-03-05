/**
 * ChatSidebar Component - Material Design 3
 */

import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Message, ToolCall, GeometryData, RasterData } from '../types.ts';
import { ToolCallDisplay } from './ToolCallDisplay.tsx';
import { streamAgentInvoke, loadGeometry, stopRuntimeSession } from '../services/api.ts';
import {
  cleanStreamingText,
  parseToolCalls,
  extractAllVisualizationData,
  extractLocationChanged,
} from '../utils/parsing.ts';
import { theme } from '../theme';
import { formatScenarioAnalysis, type ScenarioConfig } from '../utils/formatScenario';

/**
 * Load tool calls from scenario config
 */
function loadToolCallsFromConfig(config: ScenarioConfig): ToolCall[] {
  // Check if config has tool_calls defined
  const configToolCalls = (config as any).tool_calls;
  
  if (!configToolCalls || !Array.isArray(configToolCalls)) {
    console.warn('No tool_calls found in scenario config, returning empty array');
    return [];
  }

  // Convert config tool calls to ToolCall format with unique IDs
  return configToolCalls.map((tool: any, index: number) => ({
    name: tool.name,
    id: `tool_${Date.now()}_${index}`,
    params: tool.params || {},
    status: 'completed' as const,
    result: tool.result || `Completed ${tool.name}`
  }));
}

interface ChatSidebarProps {
  sessionId: string;
  scenarioId?: string;
  scenarioConfig?: ScenarioConfig | null;
  isLoadingScenario?: boolean;
  scenarioError?: string | null;
  onSessionReset: () => void;
  onGeometryUpdate: (geometry: GeometryData | null) => void;
  onRastersUpdate: (rasters: RasterData[]) => void;
  drawnGeometryMessage?: string | null;
  onDrawnGeometryMessageSent?: () => void;
  onToggleSidebar?: () => void;
}

export function ChatSidebar({
  sessionId,
  scenarioId,
  scenarioConfig,
  isLoadingScenario,
  scenarioError,
  onSessionReset,
  onGeometryUpdate,
  onRastersUpdate,
  drawnGeometryMessage,
  onDrawnGeometryMessageSent,
  onToggleSidebar,
}: ChatSidebarProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [userInput, setUserInput] = useState('');
  const [streamingText, setStreamingText] = useState('');
  const [streamingTools, setStreamingTools] = useState<ToolCall[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const scenarioDisplayedRef = useRef(false);

  useEffect(() => {
    setMessages([]);
    setUserInput('');
    setStreamingText('');
    setStreamingTools([]);
    setIsStreaming(false);
    setIsProcessing(false);
    setIsCancelling(false);
    scenarioDisplayedRef.current = false;
    // Clear any cached raster URLs from previous session
    (window as any).__lastRasterUrls = '';
  }, [sessionId]);

  // Handle drawn geometry message from map
  useEffect(() => {
    if (drawnGeometryMessage) {
      setUserInput(drawnGeometryMessage);
      // Notify parent that message has been received
      if (onDrawnGeometryMessageSent) {
        onDrawnGeometryMessageSent();
      }
    }
  }, [drawnGeometryMessage, onDrawnGeometryMessageSent]);

  // Display scenario analysis when scenario config is loaded
  useEffect(() => {
    // Show loading message while scenario is loading
    if (isLoadingScenario && messages.length === 0) {
      setMessages([
        { role: 'assistant', content: '🔥 Loading scenario data from S3...', tools: [] },
      ]);
      scenarioDisplayedRef.current = false;
      return;
    }

    // Show error message if scenario failed to load
    if (scenarioError && !scenarioDisplayedRef.current) {
      console.error('📊 Displaying scenario error');
      setMessages([
        { role: 'assistant', content: `❌ **Error loading scenario**\n\n${scenarioError}\n\nPlease try refreshing the page or contact support if the issue persists.`, tools: [] },
      ]);
      scenarioDisplayedRef.current = true;
      return;
    }

    // Display scenario analysis once when config is loaded
    if (scenarioConfig && !scenarioDisplayedRef.current) {
      console.log('📊 Displaying scenario analysis');

      const analysisText = formatScenarioAnalysis(scenarioConfig);

      // Load tool calls from scenario config
      const simulatedTools = loadToolCallsFromConfig(scenarioConfig);

      // Set initial messages with scenario-specific user question
      const userQuestion = (scenarioConfig as any).user_question || 'Analyze this area';
      setMessages([
        { role: 'user', content: userQuestion },
        { role: 'assistant', content: analysisText, tools: simulatedTools },
      ]);

      scenarioDisplayedRef.current = true;
    }
  }, [scenarioConfig, scenarioError, isLoadingScenario, messages.length]);

  const clearMapLayers = () => {
    onGeometryUpdate(null);
    onRastersUpdate([]);
  };

  const handleCancelSession = async () => {
    if (!isProcessing) return;

    setIsCancelling(true);
    console.log('🛑 Cancelling session:', sessionId);

    const success = await stopRuntimeSession(sessionId);
    
    if (success) {
      setIsStreaming(false);
      setIsProcessing(false);
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: '⚠️ Session cancelled by user.' },
      ]);
    } else {
      console.error('Failed to cancel session');
    }

    setIsCancelling(false);
  };

  const sendMessage = async () => {
    if (!userInput.trim() || isProcessing) {
      return;
    }

    const prompt = userInput;
    setUserInput('');
    setIsProcessing(true);

    setMessages((prev) => [...prev, { role: 'user', content: prompt }]);

    setIsStreaming(true);
    setStreamingText('');
    setStreamingTools([]);

    const locationChanged = extractLocationChanged('', []);

    if (locationChanged) {
      clearMapLayers();
    }

    let geometryLoaded = false;
    let lastRasterCount = 0;
    let fullResponse = '';
    let accumulatedTools: ToolCall[] = [];
    let streamCompletedNormally = false;

    try {
      for await (const event of streamAgentInvoke(prompt, sessionId, scenarioId)) {
        if (event.type === 'chunk' && event.content) {
          const cleaned = cleanStreamingText(event.content);
          if (cleaned) {
            fullResponse += cleaned;

            // Parse tools and get clean text (with JSON removed)
            let tools: ToolCall[] = [];
            let cleanText = '';
            try {
              const parsed = parseToolCalls(fullResponse);
              tools = parsed.tools;
              cleanText = parsed.cleanText;
            } catch (parseError) {
              console.error('❌ Failed to parse tools from response:', parseError);
              console.error('❌ Response text:', fullResponse.substring(0, 500));
              cleanText = fullResponse; // Fallback to showing raw response
            }

            setStreamingText(cleanText);

            // Update accumulated tools (merge with existing to avoid duplicates)
            const existingIds = new Set(accumulatedTools.map(t => t.id));
            const newTools = tools.filter(t => !existingIds.has(t.id));
            accumulatedTools = [...accumulatedTools, ...newTools];
            setStreamingTools(accumulatedTools);

            const { rasters: allRasterData, geometries: allGeometryData } =
              extractAllVisualizationData(accumulatedTools);

            // Handle geometry loading with better error handling
            if (!geometryLoaded && allGeometryData.length > 0) {
              const geometryData = allGeometryData[0];
              console.log(`📍 Loading geometry:`, geometryData.title);
              
              // Use a timeout to prevent hanging on geometry loading
              const geometryPromise = loadGeometry(geometryData.url);
              const timeoutPromise = new Promise<null>((_, reject) => 
                setTimeout(() => reject(new Error('Geometry loading timeout')), 10000)
              );
              
              try {
                const geometry = await Promise.race([geometryPromise, timeoutPromise]);
                if (geometry) {
                  geometry.locationName = geometryData.title;
                  console.log(`✅ Geometry loaded:`, geometry.locationName);
                  onGeometryUpdate(geometry);
                  geometryLoaded = true;
                } else {
                  console.error(`❌ Failed to load geometry - returned null/undefined for:`, geometryData.url);
                }
              } catch (error) {
                console.error(`❌ Exception loading geometry from ${geometryData.url}:`, error);
                console.error('❌ Geometry error details:', {
                  url: geometryData.url,
                  title: geometryData.title,
                  error: error instanceof Error ? error.message : String(error),
                });
                // Don't fail the entire stream for geometry loading issues
              }
            }

            // Handle raster updates with debouncing to prevent excessive updates
            if (allRasterData.length > 0) {
              const currentUrls = allRasterData.map((r) => r.url).sort().join('|');
              const lastUrls =
                lastRasterCount > 0 ? (window as any).__lastRasterUrls || '' : '';

              if (currentUrls !== lastUrls) {
                const rasters: RasterData[] = allRasterData.map((r) => ({
                  url: r.url,
                  name: r.title,
                  date: r.date,
                  cloudCoverage: r.cloudCoverage,
                }));

                console.log(
                  `📊 Sending ${allRasterData.length} rasters to map:`,
                  rasters.map((r) => r.name)
                );
                
                // Use setTimeout to defer raster updates and prevent blocking the stream
                setTimeout(() => {
                  onRastersUpdate(rasters);
                }, 0);
                
                lastRasterCount = allRasterData.length;
                (window as any).__lastRasterUrls = currentUrls;
              }
            }
          }
        } else if (event.type === 'error') {
          console.error('❌ Stream error from backend:', event.message);
          setMessages((prev) => [
            ...prev,
            { role: 'assistant', content: `Error: ${event.message}` },
          ]);
          return; // Exit early on error
        }
      }

      // Mark stream as completed normally
      streamCompletedNormally = true;
      console.log(`✅ Stream completed successfully. Total response length: ${fullResponse.length}, Tools: ${accumulatedTools.length}`);

      // Final extraction with error handling
      let finalRasterData: any[] = [];
      let finalGeometryData: any[] = [];
      try {
        const extracted = extractAllVisualizationData(accumulatedTools);
        finalRasterData = extracted.rasters;
        finalGeometryData = extracted.geometries;
        console.log(`📊 Final extraction: ${finalRasterData.length} rasters, ${finalGeometryData.length} geometries`);
      } catch (error) {
        console.error('❌ Failed to extract visualization data:', error);
        console.error('❌ Tools at time of failure:', accumulatedTools.map(t => ({ name: t.name, id: t.id })));
      }

      // Handle final geometry loading with timeout and better error handling
      if (finalGeometryData.length > 0 && !geometryLoaded) {
        const geometryData = finalGeometryData[0];
        console.log(`📍 [Final] Loading geometry:`, geometryData.title);
        
        const geometryPromise = loadGeometry(geometryData.url);
        const timeoutPromise = new Promise<null>((_, reject) => 
          setTimeout(() => reject(new Error('Final geometry loading timeout')), 15000)
        );
        
        try {
          const geometry = await Promise.race([geometryPromise, timeoutPromise]);
          if (geometry) {
            geometry.locationName = geometryData.title;
            console.log(`✅ [Final] Geometry loaded:`, geometry.locationName);
            onGeometryUpdate(geometry);
            geometryLoaded = true;
          } else {
            console.error(`❌ [Final] Failed to load geometry - returned null/undefined for:`, geometryData.url);
          }
        } catch (error) {
          console.error(`❌ [Final] Exception loading geometry from ${geometryData.url}:`, error);
          // Don't fail the entire response for geometry loading issues
        }
      }

      // Handle final raster updates
      if (finalRasterData.length > 0) {
        const currentUrls = finalRasterData.map((r) => r.url).sort().join('|');
        const lastUrls = (window as any).__lastRasterUrls || '';

        if (currentUrls !== lastUrls || finalRasterData.length > lastRasterCount) {
          const rasters: RasterData[] = finalRasterData.map((r) => ({
            url: r.url,
            name: r.title,
            date: r.date,
            cloudCoverage: r.cloudCoverage,
          }));

          console.log(
            `📊 [Final] Sending ${finalRasterData.length} rasters to map:`,
            rasters.map((r) => r.name)
          );
          
          // Defer final raster update to prevent blocking
          setTimeout(() => {
            onRastersUpdate(rasters);
          }, 0);
          
          lastRasterCount = finalRasterData.length;
          (window as any).__lastRasterUrls = currentUrls;
        }
      }

      const { cleanText } = parseToolCalls(fullResponse);

      const completedTools = accumulatedTools.map((tool) => ({
        ...tool,
        status: 'completed' as const,
      }));

      setMessages((prev) => [...prev, { 
        role: 'assistant', 
        content: cleanText,
        tools: completedTools.length > 0 ? completedTools : undefined
      }]);
    } catch (error) {
      console.error('❌ Exception while calling agent:', error);
      console.error('❌ Error details:', {
        message: error instanceof Error ? error.message : String(error),
        stack: error instanceof Error ? error.stack : undefined,
        streamCompletedNormally,
        responseLength: fullResponse.length,
        toolsCollected: accumulatedTools.length,
      });

      // Show user-friendly error message
      const errorMessage = error instanceof Error ? error.message : String(error);
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Error: ${errorMessage}` },
      ]);
    } finally {
      setIsStreaming(false);
      setIsProcessing(false);

      if (!streamCompletedNormally) {
        console.warn('⚠️ Stream did not complete normally. Check logs above for errors.');
      }
    }
  };

  const quickAction = (promptText: string) => {
    if (!isProcessing) {
      setUserInput(promptText);
      setTimeout(() => {
        sendMessage();
      }, 0);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        padding: theme.spacing.md,
        backgroundColor: theme.colors.surfaceVariant,
      }}
    >
      {/* Scrollable area */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          marginBottom: theme.spacing.md,
          paddingRight: theme.spacing.sm,
        }}
      >
        {/* Chat messages */}
        <div
          style={{
            padding: theme.spacing.md,
            backgroundColor: theme.colors.surface,
            borderRadius: theme.borderRadius.md,
            boxShadow: theme.elevation.level1,
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: theme.spacing.md,
            }}
          >
            <h3
              style={{
                ...theme.typography.titleLarge,
                marginTop: 0,
                marginBottom: 0,
                color: theme.colors.onSurface,
              }}
            >
              Conversation
            </h3>

            {onToggleSidebar && (
              <button
                onClick={onToggleSidebar}
                style={{
                  padding: `${theme.spacing.sm} ${theme.spacing.md}`,
                  backgroundColor: 'transparent',
                  color: theme.colors.secondary,
                  border: `1px solid ${theme.colors.outline}`,
                  borderRadius: theme.borderRadius.md,
                  cursor: 'pointer',
                  ...theme.typography.labelLarge,
                  transition: theme.transitions.short,
                  fontSize: '18px',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = theme.states.hover;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = 'transparent';
                }}
                title="Hide chat"
              >
                ☰
              </button>
            )}
          </div>

          {messages.length === 0 ? (
            <div
              style={{
                padding: theme.spacing.md,
                backgroundColor: theme.colors.surfaceVariant,
                borderRadius: theme.borderRadius.sm,
                ...theme.typography.bodyMedium,
                color: theme.colors.secondary,
                lineHeight: '1.6',
              }}
            >
              <div style={{ marginBottom: theme.spacing.sm }}>
                Analyze any location on Earth using Sentinel-2 satellite imagery. I can assess <strong>vegetation health</strong>, <strong>wildfire damage</strong>, <strong>water levels</strong>, and <strong>track changes over time</strong>.
              </div>
              <div style={{ fontSize: '12px', color: theme.colors.onSurface }}>
                Try a <strong>location name</strong>, <strong>coordinates</strong> (lat/lon), or <strong>draw a polygon</strong> on the map.
              </div>
            </div>
          ) : (
            <div>
              {messages.map((msg, index) => (
                <div key={index} style={{ marginBottom: theme.spacing.md }}>
                  <div
                    style={{
                      ...theme.typography.labelLarge,
                      marginBottom: theme.spacing.xs,
                      color: theme.colors.onSurface,
                    }}
                  >
                    {msg.role === 'user' ? 'User' : 'Assistant'}
                  </div>
                  <div
                    style={{
                      padding: theme.spacing.md,
                      backgroundColor:
                        msg.role === 'user'
                          ? theme.colors.primaryContainer
                          : theme.colors.surfaceVariant,
                      borderRadius: theme.borderRadius.sm,
                      ...theme.typography.bodyMedium,
                    }}
                    className="markdown-content"
                  >
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  </div>
                  
                  {/* Inline tool calls for this message */}
                  {msg.tools && msg.tools.length > 0 && (
                    <div style={{ marginTop: theme.spacing.sm, marginLeft: theme.spacing.md }}>
                      <ToolCallDisplay tools={msg.tools} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Streaming message */}
          {isStreaming && (
            <div style={{ marginTop: theme.spacing.md, minHeight: '60px' }}>
              <div
                style={{
                  ...theme.typography.labelLarge,
                  marginBottom: theme.spacing.xs,
                  color: theme.colors.onSurface,
                }}
              >
                Assistant (streaming...)
              </div>
              
              <div
                style={{
                  padding: theme.spacing.md,
                  backgroundColor: theme.colors.surfaceVariant,
                  borderRadius: theme.borderRadius.sm,
                  ...theme.typography.bodyMedium,
                }}
                className="markdown-content"
              >
                {streamingText ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {parseToolCalls(streamingText).cleanText}
                  </ReactMarkdown>
                ) : (
                  'Thinking...'
                )}
              </div>
              
              {/* Streaming tools - below the response */}
              {streamingTools.length > 0 && (
                <div style={{ marginTop: theme.spacing.sm, marginLeft: theme.spacing.md }}>
                  <ToolCallDisplay tools={streamingTools} />
                </div>
              )}
            </div>
          )}

          {/* Quick actions */}
          <div style={{ marginTop: theme.spacing.lg }}>
            <h4
              style={{
                ...theme.typography.titleMedium,
                marginBottom: theme.spacing.sm,
                color: theme.colors.onSurface,
              }}
            >
              Quick Actions
            </h4>
            <div style={{ display: 'flex', gap: theme.spacing.sm, flexWrap: 'wrap' }}>
              <button
                onClick={() => quickAction('Show vegetation health for Central Park, New York')}
                disabled={isProcessing}
                style={{
                  padding: `${theme.spacing.sm} ${theme.spacing.md}`,
                  backgroundColor: isProcessing ? theme.colors.outline : theme.colors.primary,
                  color: theme.colors.onPrimary,
                  border: 'none',
                  borderRadius: theme.borderRadius.md,
                  cursor: isProcessing ? 'not-allowed' : 'pointer',
                  ...theme.typography.labelLarge,
                  opacity: isProcessing ? theme.states.disabled : 1,
                  transition: theme.transitions.short,
                  boxShadow: isProcessing ? 'none' : theme.elevation.level1,
                }}
                onMouseEnter={(e) => {
                  if (!isProcessing) {
                    e.currentTarget.style.boxShadow = theme.elevation.level2;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isProcessing) {
                    e.currentTarget.style.boxShadow = theme.elevation.level1;
                  }
                }}
              >
                Vegetation
              </button>
              <button
                onClick={() => quickAction('Assess wildfire damage near Pacific Palisades, Los Angeles in January 2025')}
                disabled={isProcessing}
                style={{
                  padding: `${theme.spacing.sm} ${theme.spacing.md}`,
                  backgroundColor: isProcessing ? theme.colors.outline : theme.colors.primary,
                  color: theme.colors.onPrimary,
                  border: 'none',
                  borderRadius: theme.borderRadius.md,
                  cursor: isProcessing ? 'not-allowed' : 'pointer',
                  ...theme.typography.labelLarge,
                  opacity: isProcessing ? theme.states.disabled : 1,
                  transition: theme.transitions.short,
                  boxShadow: isProcessing ? 'none' : theme.elevation.level1,
                }}
                onMouseEnter={(e) => {
                  if (!isProcessing) {
                    e.currentTarget.style.boxShadow = theme.elevation.level2;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isProcessing) {
                    e.currentTarget.style.boxShadow = theme.elevation.level1;
                  }
                }}
              >
                Wildfire
              </button>
              <button
                onClick={() =>
                  quickAction('Compare water levels for Folsom Lake, California 2021 vs 2022')
                }
                disabled={isProcessing}
                style={{
                  padding: `${theme.spacing.sm} ${theme.spacing.md}`,
                  backgroundColor: isProcessing ? theme.colors.outline : theme.colors.primary,
                  color: theme.colors.onPrimary,
                  border: 'none',
                  borderRadius: theme.borderRadius.md,
                  cursor: isProcessing ? 'not-allowed' : 'pointer',
                  ...theme.typography.labelLarge,
                  opacity: isProcessing ? theme.states.disabled : 1,
                  transition: theme.transitions.short,
                  boxShadow: isProcessing ? 'none' : theme.elevation.level1,
                }}
                onMouseEnter={(e) => {
                  if (!isProcessing) {
                    e.currentTarget.style.boxShadow = theme.elevation.level2;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isProcessing) {
                    e.currentTarget.style.boxShadow = theme.elevation.level1;
                  }
                }}
              >
                Drought
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Input area */}
      <div>
        <textarea
          value={userInput}
          onChange={(e) => setUserInput(e.target.value)}
          onKeyPress={handleKeyPress}
          placeholder="Run earth analyses..."
          disabled={isProcessing}
          style={{
            width: '100%',
            minHeight: '60px',
            padding: theme.spacing.md,
            ...theme.typography.bodyMedium,
            borderRadius: theme.borderRadius.sm,
            border: `1px solid ${theme.colors.outline}`,
            marginBottom: theme.spacing.sm,
            resize: 'vertical',
            backgroundColor: theme.colors.surface,
            color: theme.colors.onSurface,
            transition: theme.transitions.short,
          }}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = theme.colors.primary;
            e.currentTarget.style.outline = `2px solid ${theme.colors.primary}`;
            e.currentTarget.style.outlineOffset = '0';
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = theme.colors.outline;
            e.currentTarget.style.outline = 'none';
          }}
        />

        <div style={{ display: 'flex', gap: theme.spacing.sm, alignItems: 'center' }}>
          <button
            onClick={sendMessage}
            disabled={isProcessing || !userInput.trim()}
            style={{
              padding: `${theme.spacing.md} ${theme.spacing.lg}`,
              backgroundColor:
                isProcessing || !userInput.trim() ? theme.colors.outline : theme.colors.primary,
              color: theme.colors.onPrimary,
              border: 'none',
              borderRadius: theme.borderRadius.md,
              cursor: isProcessing || !userInput.trim() ? 'not-allowed' : 'pointer',
              ...theme.typography.labelLarge,
              opacity: isProcessing || !userInput.trim() ? theme.states.disabled : 1,
              transition: theme.transitions.short,
              boxShadow:
                isProcessing || !userInput.trim() ? 'none' : theme.elevation.level2,
              display: 'flex',
              alignItems: 'center',
              gap: theme.spacing.sm,
            }}
            onMouseEnter={(e) => {
              if (!isProcessing && userInput.trim()) {
                e.currentTarget.style.boxShadow = theme.elevation.level3;
              }
            }}
            onMouseLeave={(e) => {
              if (!isProcessing && userInput.trim()) {
                e.currentTarget.style.boxShadow = theme.elevation.level2;
              }
            }}
          >
            {isProcessing ? (
              <>
                Sending
                <div
                  style={{
                    width: '16px',
                    height: '16px',
                    border: '3px solid rgba(255, 255, 255, 0.5)',
                    borderTopColor: '#FFFFFF',
                    borderBottomColor: '#FFFFFF',
                    borderRadius: '50%',
                    animation: 'spin 0.8s linear infinite',
                  }}
                />
              </>
            ) : (
              'Send'
            )}
          </button>

          <div
            style={{
              flex: 1,
              ...theme.typography.bodyMedium,
              color: theme.colors.secondary,
              fontSize: '12px',
            }}
          >
            Session: {sessionId.substring(0, 8)}...
          </div>

          <button
            onClick={handleCancelSession}
            disabled={!isProcessing || isCancelling}
            style={{
              padding: `${theme.spacing.md} ${theme.spacing.md}`,
              backgroundColor: 'transparent',
              color: isProcessing && !isCancelling ? theme.colors.error : theme.colors.secondary,
              border: `1px solid ${isProcessing && !isCancelling ? theme.colors.error : theme.colors.outline}`,
              borderRadius: theme.borderRadius.md,
              cursor: isProcessing && !isCancelling ? 'pointer' : 'not-allowed',
              ...theme.typography.labelLarge,
              opacity: !isProcessing || isCancelling ? theme.states.disabled : 1,
              transition: theme.transitions.short,
            }}
            onMouseEnter={(e) => {
              if (isProcessing && !isCancelling) {
                e.currentTarget.style.backgroundColor = theme.states.hover;
              }
            }}
            onMouseLeave={(e) => {
              if (isProcessing && !isCancelling) {
                e.currentTarget.style.backgroundColor = 'transparent';
              }
            }}
          >
            {isCancelling ? 'Cancelling...' : 'Cancel'}
          </button>

          <button
            onClick={onSessionReset}
            style={{
              padding: `${theme.spacing.md} ${theme.spacing.md}`,
              backgroundColor: 'transparent',
              color: theme.colors.secondary,
              border: `1px solid ${theme.colors.outline}`,
              borderRadius: theme.borderRadius.md,
              cursor: 'pointer',
              ...theme.typography.labelLarge,
              transition: theme.transitions.short,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = theme.states.hover;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent';
            }}
          >
            Reset
          </button>
        </div>
      </div>
    </div>
  );
}
