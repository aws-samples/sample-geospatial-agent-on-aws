import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { v4 as uuidv4 } from 'uuid';
import { MapView } from '../components/MapView';
import { ChatSidebar } from '../components/ChatSidebar';
import type { GeometryData, RasterData } from '../types';
import { theme } from '../theme';
import { getIdToken } from '../utils/auth';

interface ScenarioToolCall {
  name: string;
  params: Record<string, any>;
  result: string;
}

interface ScenarioConfig {
  id: string;
  name: string;
  description: string;
  location: string;
  user_question?: string;
  index_type?: 'nbr' | 'ndvi' | 'ndwi';
  dates: {
    before?: string;
    after?: string;
    fire_start?: string;
  };
  narrative: string;
  tool_calls?: ScenarioToolCall[];
  assets: {
    geometry_url: string;
    before: {
      tci: string;
      nbr?: string;
      ndvi?: string;
      ndwi?: string;
      [key: string]: string | undefined;
    };
    after: {
      tci: string;
      nbr?: string;
      ndvi?: string;
      ndwi?: string;
      [key: string]: string | undefined;
    };
  };
}

export function Chat() {
  const [searchParams] = useSearchParams();
  const scenarioId = searchParams.get('scenario');

  const [sessionId, setSessionId] = useState(uuidv4());
  const [currentGeometry, setCurrentGeometry] = useState<GeometryData | null>(null);
  const [currentRasters, setCurrentRasters] = useState<RasterData[]>([]);
  const [drawnGeometryMessage, setDrawnGeometryMessage] = useState<string | null>(null);
  const [scenarioConfig, setScenarioConfig] = useState<ScenarioConfig | null>(null);
  const [isLoadingScenario, setIsLoadingScenario] = useState(false);
  const [scenarioError, setScenarioError] = useState<string | null>(null);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);

  // Load scenario when scenarioId is present
  useEffect(() => {
    if (!scenarioId) {
      setScenarioConfig(null);
      setScenarioError(null);
      return;
    }

    const loadScenario = async () => {
      setIsLoadingScenario(true);
      setScenarioError(null);

      try {
        // Get API URL with same logic as api.ts
        const getApiUrl = () => {
          if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
          if (import.meta.env.VITE_DEV_MODE === 'true' || window.location.hostname === 'localhost') {
            return 'http://localhost:3001';
          }
          return window.location.origin;
        };
        const API_URL = getApiUrl();

        // Fetch scenario config
        const idToken = getIdToken();
        const headers: HeadersInit = { 'Content-Type': 'application/json' };
        if (idToken) {
          headers['Authorization'] = `Bearer ${idToken}`;
        }
        const response = await fetch(`${API_URL}/api/scenario/${scenarioId}`, { headers });
        if (!response.ok) {
          throw new Error(`Failed to load scenario: ${response.statusText}`);
        }

        const config: ScenarioConfig = await response.json();
        console.log('✅ Scenario config loaded:', config.name);
        setScenarioConfig(config);

        // Pre-load geometry
        const { loadGeometry } = await import('../services/api');
        const geometry = await loadGeometry(config.assets.geometry_url);
        if (geometry) {
          console.log('✅ Geometry pre-loaded');
          setCurrentGeometry(geometry);
        }

        // Pre-load key rasters with explicit zIndex for stacking
        // Higher zIndex = on top visually
        const rasters: RasterData[] = [];

        // TCI Before (pre-fire sat image) - zIndex 4 (top)
        if (config.assets.before?.tci) {
          rasters.push({
            url: config.assets.before.tci,
            name: 'TCI Before',
            date: config.dates.before,
            zIndex: 4,
          });
        }

        // TCI After (post-fire sat image) - zIndex 3
        if (config.assets.after?.tci) {
          rasters.push({
            url: config.assets.after.tci,
            name: 'TCI After',
            date: config.dates.after,
            zIndex: 3,
          });
        }

        // Load appropriate index based on index_type
        const indexType = config.index_type || 'nbr'; // Default to NBR for backward compatibility
        console.log(`📊 Loading index type: ${indexType}`);
        console.log('📋 Full config:', config);
        console.log('Available assets:', {
          before: Object.keys(config.assets.before || {}),
          after: Object.keys(config.assets.after || {}),
        });
        console.log('NDVI URLs:', {
          before: config.assets.before?.ndvi,
          after: config.assets.after?.ndvi,
        });
        
        if (indexType === 'ndvi') {
          // NDVI - Normalized Difference Vegetation Index (for vegetation/deforestation analysis)
          if (config.assets.before?.ndvi) {
            rasters.push({
              url: config.assets.before.ndvi,
              name: 'NDVI Before',
              date: config.dates.before,
              zIndex: 2,
            });
          }
          if (config.assets.after?.ndvi) {
            rasters.push({
              url: config.assets.after.ndvi,
              name: 'NDVI After',
              date: config.dates.after,
              zIndex: 1,
            });
          }
        } else if (indexType === 'ndwi') {
          // NDWI - Normalized Difference Water Index (for water/drought analysis)
          if (config.assets.before?.ndwi) {
            rasters.push({
              url: config.assets.before.ndwi,
              name: 'NDWI Before',
              date: config.dates.before,
              zIndex: 2,
            });
          }
          if (config.assets.after?.ndwi) {
            rasters.push({
              url: config.assets.after.ndwi,
              name: 'NDWI After',
              date: config.dates.after,
              zIndex: 1,
            });
          }
        } else if (indexType === 'nbr') {
          // NBR - Normalized Burn Ratio (for wildfire/burn analysis)
          if (config.assets.before?.nbr) {
            rasters.push({
              url: config.assets.before.nbr,
              name: 'NBR Before',
              date: config.dates.before,
              zIndex: 2,
            });
          }
          if (config.assets.after?.nbr) {
            rasters.push({
              url: config.assets.after.nbr,
              name: 'NBR After',
              date: config.dates.after,
              zIndex: 1,
            });
          }
        }

        console.log('✅ Pre-loading rasters:', rasters.map(r => `${r.name} (z:${r.zIndex})`));
        setCurrentRasters(rasters);

      } catch (error) {
        console.error('❌ Error loading scenario:', error);
        const errorMessage = error instanceof Error ? error.message : 'Failed to load scenario';
        setScenarioError(errorMessage);
        setScenarioConfig(null);
      } finally {
        setIsLoadingScenario(false);
      }
    };

    loadScenario();
  }, [scenarioId]);

  const handleSessionReset = () => {
    setSessionId(uuidv4());
    setCurrentGeometry(null);
    setCurrentRasters([]);
    setDrawnGeometryMessage(null);
    setScenarioConfig(null);
    setScenarioError(null);
    setIsLoadingScenario(false);
  };

  const handleDrawnGeometry = (geojson: any) => {
    // Format the GeoJSON as a message to send to chat
    const message = `Analyze this area:\n\`\`\`json\n${JSON.stringify(geojson, null, 2)}\n\`\`\``;
    setDrawnGeometryMessage(message);
  };

  return (
    <div style={{ flex: 1, display: 'flex', overflow: 'hidden', position: 'relative' }}>
      {/* Left sidebar - Chat */}
      <div
        style={{
          width: isSidebarCollapsed ? '0' : '30%',
          minWidth: isSidebarCollapsed ? '0' : '350px',
          maxWidth: isSidebarCollapsed ? '0' : '500px',
          backgroundColor: theme.colors.surfaceVariant,
          borderRight: isSidebarCollapsed ? 'none' : `1px solid ${theme.colors.outline}`,
          overflow: 'hidden',
          transition: 'width 300ms ease-in-out, min-width 300ms ease-in-out',
          display: isSidebarCollapsed ? 'none' : 'block',
        }}
      >
        <ChatSidebar
          sessionId={sessionId}
          scenarioId={scenarioId || undefined}
          scenarioConfig={scenarioConfig}
          isLoadingScenario={isLoadingScenario}
          scenarioError={scenarioError}
          onSessionReset={handleSessionReset}
          onGeometryUpdate={setCurrentGeometry}
          onRastersUpdate={setCurrentRasters}
          drawnGeometryMessage={drawnGeometryMessage}
          onDrawnGeometryMessageSent={() => setDrawnGeometryMessage(null)}
          onToggleSidebar={() => setIsSidebarCollapsed(true)}
        />
      </div>

      {/* Right area - Map */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {/* Toggle sidebar button - only show when collapsed */}
        {isSidebarCollapsed && (
          <button
            onClick={() => setIsSidebarCollapsed(false)}
            style={{
              position: 'absolute',
              top: '16px',
              left: '16px',
              zIndex: 1000,
              width: '40px',
              height: '40px',
              backgroundColor: '#FFFFFF',
              border: 'none',
              borderRadius: '8px',
              boxShadow: '0 4px 8px rgba(0,0,0,0.16)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '22px',
              transition: 'all 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
              fontFamily: 'Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = '#F5F5F5';
              e.currentTarget.style.transform = 'scale(1.05)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = '#FFFFFF';
              e.currentTarget.style.transform = 'scale(1)';
            }}
            title="Show chat"
          >
            ☰
          </button>
        )}

        <MapView
          geometry={currentGeometry}
          rasters={currentRasters}
          onDrawnGeometry={handleDrawnGeometry}
        />
      </div>
    </div>
  );
}
