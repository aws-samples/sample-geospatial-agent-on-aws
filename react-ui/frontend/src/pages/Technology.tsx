import { useState } from 'react';
import { theme } from '../theme';

// Expandable section component
function ExpandableSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div
      style={{
        backgroundColor: theme.colors.surface,
        borderRadius: theme.borderRadius.md,
        boxShadow: theme.elevation.level1,
        marginBottom: theme.spacing.md,
        overflow: 'hidden',
      }}
    >
      <button
        onClick={() => setIsOpen(!isOpen)}
        style={{
          width: '100%',
          padding: theme.spacing.md,
          backgroundColor: 'transparent',
          border: 'none',
          cursor: 'pointer',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          ...theme.typography.titleMedium,
          color: theme.colors.onBackground,
        }}
      >
        <span>{title}</span>
        <span style={{ fontSize: '1.2em' }}>{isOpen ? '−' : '+'}</span>
      </button>
      {isOpen && (
        <div
          style={{
            padding: theme.spacing.md,
            paddingTop: 0,
            ...theme.typography.bodyMedium,
            color: theme.colors.secondary,
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}

export function Technology() {
  return (
    <div className="page-content" style={{ padding: theme.spacing.xxl }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto' }}>
        {/* Title */}
        <h1
          style={{
            ...theme.typography.headlineLarge,
            marginBottom: theme.spacing.lg,
            color: theme.colors.onBackground,
          }}
        >
          Technology
        </h1>

        {/* Geospatial Agent on AWS Section */}
        <section style={{ marginBottom: theme.spacing.xxl }}>
          <h2
            style={{
              ...theme.typography.headlineMedium,
              marginBottom: theme.spacing.md,
              color: theme.colors.onBackground,
            }}
          >
            Geospatial Agent on AWS
          </h2>
          <p
            style={{
              ...theme.typography.bodyLarge,
              color: theme.colors.secondary,
              marginBottom: theme.spacing.md,
              lineHeight: 1.6,
            }}
          >
            <strong>Geospatial Agent on AWS</strong> is designed to democratize access to and
            large-scale analysis of Earth Data (geo fences, satellite imagery, aerial imagery, vector layers). 
            By enabling natural language queries, <strong>we make powerful Earth 
            observation capabilities accessible to anyone who can describe
            what they need to know</strong> (e.g., "Show me the impact of the 2025 LA Wildfires
            and estimate the size of the affected area").
          </p>
          <p
            style={{
              ...theme.typography.bodyLarge,
              color: theme.colors.secondary,
              marginBottom: theme.spacing.md,
              lineHeight: 1.6,
            }}
          >
            The project uses an intelligent software agent (<strong>GeoAgent</strong>) that can <strong>autonomously plan and execute
            geocoding, data collection and analytics tasks for any location on earth</strong>. It achieves
            this by accessing a set of tools (think Python functions) that enable the agent to perform the required actions.
          </p>
          {/* <p
            style={{
              ...theme.typography.bodyLarge,
              color: theme.colors.secondary,
              marginBottom: theme.spacing.md,
              lineHeight: 1.6,
            }}
          >
            At each turn, the agent can use the tools to perform a task, and then use the results
            to update the context for the next task. This allows the agent to perform a sequence of
            tasks, and then return the final result to the user.
          </p> */}
          <p
            style={{
              ...theme.typography.bodyLarge,
              color: theme.colors.secondary,
              marginBottom: theme.spacing.lg,
              lineHeight: 1.6,
            }}
          >
            At its core, the agent is powered by an LLM (
            <a
              href="https://aws.amazon.com/about-aws/whats-new/2026/02/claude-sonnet-4.6-available-in-amazon-bedrock/"
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: '#1976d2', textDecoration: 'underline' }}
            >
              Claude Sonnet 4.6 in Amazon Bedrock
            </a>
            ) that is provided with concrete instructions via specialized prompting techniques that enable it to "think". At each turn
            the LLM receives the following information:
          </p>

          {/* System Prompt Expandable */}
          <ExpandableSection title="System Prompt">
            <pre
              style={{
                backgroundColor: theme.colors.surfaceVariant,
                padding: theme.spacing.md,
                borderRadius: theme.borderRadius.sm,
                overflow: 'auto',
                fontSize: '0.85em',
                lineHeight: 1.4,
              }}
            >
              {`You are GeoAgent, an AI assistant specializing in geospatial analysis using satellite imagery.

CORE CAPABILITIES:
- Geocode locations and retrieve precise boundaries
- Access and analyze Sentinel-2 satellite imagery
- Calculate environmental indices (NDVI, NDWI, NBR)
- Quantify changes over time (deforestation, drought, wildfire damage)
- Generate visualizations for map display

RESPONSE STYLE:
- Be concise and direct - avoid lengthy explanations unless asked
- State what you're doing in 1-2 sentences max
- Focus on results and quantifiable metrics
- Always provide specific numbers when available

WORKFLOW PATTERNS:

📍 COORDINATE-BASED ANALYSIS (when user provides lat/lon):
   1. create_bbox_from_coordinates(location_name, lat, lon) → geometry
   2. get_rasters(location, geometry_s3_url, date, ...) → satellite bands
   3. run_bandmath(location, index_type, date_str, ...) → analysis
   4. display_visual(...) → show results

🌍 LOCATION-BASED ANALYSIS (when user provides place name):
   1. PARALLEL: search_places(location) + find_location_boundary(location)
   2. get_best_geometry(location, osm_s3_url, lat, lon) → validated geometry
   3. get_rasters(location, geometry_s3_url, date, ...) → satellite bands
   4. run_bandmath(location, index_type, date_str, ...) → analysis
   5. display_visual(...) → show results

ANALYSIS TYPE SELECTION:
- WATER: floods, drought, reservoirs → NDWI (Normalized Difference Water Index)
- VEGETATION: forests, crops, deforestation → NDVI (Normalized Difference Vegetation Index)
- FIRE: wildfires, burn scars → NBR (Normalized Burn Ratio)

CHANGE DETECTION:
- Always compare before/after imagery for impact analysis
- Calculate percentage changes and absolute differences
- Use calculator tool for quantitative metrics`}
            </pre>
            <p style={{ marginTop: theme.spacing.sm, fontStyle: 'italic' }}>
              Full prompt includes detailed tool usage patterns, error handling, and 30+ example
              scenarios
            </p>
          </ExpandableSection>

          {/* Available Tools Expandable */}
          <ExpandableSection title="Available Tools">
            <div style={{ marginBottom: theme.spacing.md }}>
              <strong>Utility Tools:</strong>
              <ul style={{ marginTop: theme.spacing.xs, paddingLeft: theme.spacing.lg }}>
                <li>
                  <code>display_visual</code>: Send visualizations (geometries, satellite imagery, analysis results) to frontend for map display
                </li>
                <li>
                  <code>calculator</code>: Perform mathematical calculations for change analysis and metric computation
                </li>
                <li>
                  <code>list_session_assets</code>: List previously generated assets in current session
                </li>
              </ul>
            </div>
            <div style={{ marginBottom: theme.spacing.md }}>
              <strong>Geocoding Tools:</strong>
              <ul style={{ marginTop: theme.spacing.xs, paddingLeft: theme.spacing.lg }}>
                <li>
                  <code>search_places</code>: Find coordinates for location names using Amazon Location Service (MCP)
                </li>
                <li>
                  <code>find_location_boundary</code>: Retrieve precise OSM polygon boundaries for named places
                </li>
                <li>
                  <code>get_best_geometry</code>: Validate and select best geometry (OSM polygon vs bbox) based on coordinates
                </li>
                <li>
                  <code>create_bbox_from_coordinates</code>: Create 2km bounding box from lat/lon coordinates
                </li>
              </ul>
            </div>
            <div style={{ marginBottom: theme.spacing.md }}>
              <strong>Data Retrieval Tools:</strong>
              <ul style={{ marginTop: theme.spacing.xs, paddingLeft: theme.spacing.lg }}>
                <li>
                  <code>get_rasters</code>: Search and retrieve Sentinel-2 satellite imagery (returns TCI, Red, Green, NIR08, SWIR2 bands)
                </li>
              </ul>
            </div>
            <div>
              <strong>Analysis Tools:</strong>
              <ul style={{ marginTop: theme.spacing.xs, paddingLeft: theme.spacing.lg }}>
                <li>
                  <code>run_bandmath</code>: Universal band math calculator for any spectral index (NDVI, NDWI, NBR, custom formulas)
                </li>
                {/* <li>
                  <code>get_ndvi_stats</code>: Calculate Normalized Difference Vegetation Index for vegetation health analysis
                </li>
                <li>
                  <code>get_ndwi_stats</code>: Calculate Normalized Difference Water Index for water body and drought analysis
                </li>
                <li>
                  <code>get_nbr_stats</code>: Calculate Normalized Burn Ratio for wildfire damage assessment
                </li> */}
                <li>
                  <code>calculate_environmental_impact</code>: Calculate environmental impact metrics from analysis results
                </li>
              </ul>
            </div>
          </ExpandableSection>

          <ExpandableSection title="User Query">
            <div
              style={{
                padding: theme.spacing.md,
                backgroundColor: theme.colors.surface,
                borderLeft: `4px solid ${theme.colors.primary}`,
                borderRadius: theme.borderRadius.sm,
              }}
            >
              <p style={{ fontStyle: 'italic', margin: 0, lineHeight: 1.6 }}>
                "Show me the impact of the 2025 LA Wildfires and
              estimate the size of the affected area."
              </p>
            </div>
          </ExpandableSection>

          {/* Example Tool Use Trace Expandable */}
          <ExpandableSection title="Conversation Context">
            <div style={{ lineHeight: 1.8 }}>
              {/* Turn 1 */}
              <div
                style={{
                  marginBottom: theme.spacing.lg,
                  paddingBottom: theme.spacing.md,
                  borderBottom: `1px solid ${theme.colors.surfaceVariant}`,
                }}
              >
                <p style={{ marginBottom: theme.spacing.sm, fontStyle: 'italic' }}>
                  <strong>Agent:</strong> I'll analyze the LA wildfire impact by getting the area
                  boundary, retrieving before/after satellite imagery, and calculating burn
                  severity.
                </p>
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginBottom: theme.spacing.xs,
                  }}
                >
                  <strong>🔧 Tool:</strong> <code>search_places("Pacific Palisades, Los Angeles")</code>
                </div>
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginBottom: theme.spacing.xs,
                  }}
                >
                  <strong>🔧 Tool:</strong>{' '}
                  <code>find_location_boundary("Pacific Palisades, Los Angeles")</code>
                </div>
                <p style={{ marginTop: theme.spacing.sm, fontSize: '0.9em' }}>
                  ✓ Coordinates: 34.0522°N, 118.5212°W
                  <br />✓ OSM boundary polygon retrieved
                </p>
              </div>

              {/* Turn 2 */}
              <div
                style={{
                  marginBottom: theme.spacing.lg,
                  paddingBottom: theme.spacing.md,
                  borderBottom: `1px solid ${theme.colors.surfaceVariant}`,
                }}
              >
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginBottom: theme.spacing.xs,
                  }}
                >
                  <strong>🔧 Tool:</strong>{' '}
                  <code>get_best_geometry("Pacific Palisades", osm_s3_url, 34.0522, -118.5212)</code>
                </div>
                <p style={{ marginTop: theme.spacing.sm, fontSize: '0.9em' }}>
                  ✓ Validated geometry: 10.2 km² area
                </p>
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginTop: theme.spacing.sm,
                  }}
                >
                  <strong>🔧 Tool:</strong>{' '}
                  <code>display_visual(geometry_s3_url, "Boundary of Pacific Palisades")</code>
                </div>
              </div>

              {/* Turn 3 */}
              <div
                style={{
                  marginBottom: theme.spacing.lg,
                  paddingBottom: theme.spacing.md,
                  borderBottom: `1px solid ${theme.colors.surfaceVariant}`,
                }}
              >
                <p style={{ marginBottom: theme.spacing.sm, fontStyle: 'italic' }}>
                  <strong>Agent:</strong> Now retrieving satellite imagery for before (Jan 5) and
                  after (Jan 15) the fire event.
                </p>
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginBottom: theme.spacing.xs,
                  }}
                >
                  <strong>🔧 Tool:</strong>{' '}
                  <code>
                    get_rasters("Pacific Palisades", geometry_s3_url, date="2025-01-05",
                    max_cloud=30)
                  </code>
                </div>
                <p style={{ marginTop: theme.spacing.sm, fontSize: '0.9em' }}>
                  ✓ Pre-fire imagery: TCI, NIR08, SWIR2 bands (Jan 5, 2025)
                </p>
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginTop: theme.spacing.sm,
                  }}
                >
                  <strong>🔧 Tool:</strong>{' '}
                  <code>display_visual(tci_before_s3_url, "Satellite Image - Jan 5, 2025")</code>
                </div>
              </div>

              {/* Turn 4 */}
              <div
                style={{
                  marginBottom: theme.spacing.lg,
                  paddingBottom: theme.spacing.md,
                  borderBottom: `1px solid ${theme.colors.surfaceVariant}`,
                }}
              >
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginBottom: theme.spacing.xs,
                  }}
                >
                  <strong>🔧 Tool:</strong>{' '}
                  <code>
                    get_rasters("Pacific Palisades", geometry_s3_url, date="2025-01-15",
                    max_cloud=30)
                  </code>
                </div>
                <p style={{ marginTop: theme.spacing.sm, fontSize: '0.9em' }}>
                  ✓ Post-fire imagery: TCI, NIR08, SWIR2 bands (Jan 15, 2025)
                </p>
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginTop: theme.spacing.sm,
                  }}
                >
                  <strong>🔧 Tool:</strong>{' '}
                  <code>display_visual(tci_after_s3_url, "Satellite Image - Jan 15, 2025")</code>
                </div>
              </div>

              {/* Turn 5 */}
              <div
                style={{
                  marginBottom: theme.spacing.lg,
                  paddingBottom: theme.spacing.md,
                  borderBottom: `1px solid ${theme.colors.surfaceVariant}`,
                }}
              >
                <p style={{ marginBottom: theme.spacing.sm, fontStyle: 'italic' }}>
                  <strong>Agent:</strong> Calculating NBR (Normalized Burn Ratio) to assess fire
                  damage severity.
                </p>
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginBottom: theme.spacing.xs,
                  }}
                >
                  <strong>🔧 Tool:</strong>{' '}
                  <code>
                    get_nbr_stats(location, nir08_before, swir2_before, date_str="2025-01-05",
                    geometry_s3_url)
                  </code>
                </div>
                <p style={{ marginTop: theme.spacing.sm, fontSize: '0.9em' }}>
                  ✓ Pre-fire NBR baseline established
                </p>
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginTop: theme.spacing.sm,
                  }}
                >
                  <strong>🔧 Tool:</strong>{' '}
                  <code>display_visual(nbr_before_s3_url, "NBR Before Fire - Jan 5, 2025")</code>
                </div>
              </div>

              {/* Turn 6 */}
              <div
                style={{
                  marginBottom: theme.spacing.lg,
                  paddingBottom: theme.spacing.md,
                  borderBottom: `1px solid ${theme.colors.surfaceVariant}`,
                }}
              >
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginBottom: theme.spacing.xs,
                  }}
                >
                  <strong>🔧 Tool:</strong>{' '}
                  <code>
                    get_nbr_stats(location, nir08_after, swir2_after, date_str="2025-01-15",
                    geometry_s3_url)
                  </code>
                </div>
                <div
                  style={{
                    marginTop: theme.spacing.sm,
                    padding: theme.spacing.sm,
                    backgroundColor: theme.colors.surfaceVariant,
                    borderRadius: theme.borderRadius.sm,
                    fontSize: '0.9em',
                  }}
                >
                  <strong>✓ Burn Severity Analysis:</strong>
                  <ul style={{ marginTop: theme.spacing.xs, paddingLeft: theme.spacing.lg }}>
                    <li>High severity: 3.2 km² (31%)</li>
                    <li>Moderate severity: 4.1 km² (40%)</li>
                    <li>Low severity: 2.0 km² (20%)</li>
                    <li>Unburned: 0.9 km² (9%)</li>
                  </ul>
                </div>
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginTop: theme.spacing.sm,
                  }}
                >
                  <strong>🔧 Tool:</strong>{' '}
                  <code>display_visual(nbr_after_s3_url, "NBR After Fire - Jan 15, 2025")</code>
                </div>
              </div>

              {/* Turn 7 */}
              <div style={{ marginBottom: theme.spacing.lg }}>
                <p style={{ marginBottom: theme.spacing.sm, fontStyle: 'italic' }}>
                  <strong>Agent:</strong> Let me calculate the total burned area by summing high,
                  moderate, and low severity zones.
                </p>
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginBottom: theme.spacing.xs,
                  }}
                >
                  <strong>🔧 Tool:</strong> <code>calculator("3.2 + 4.1 + 2.0")</code>
                </div>
                <p style={{ marginTop: theme.spacing.sm, fontSize: '0.9em' }}>
                  ✓ Result: 9.3 km²
                </p>
                <div
                  style={{
                    backgroundColor: theme.colors.surfaceVariant,
                    padding: theme.spacing.sm,
                    borderRadius: theme.borderRadius.sm,
                    marginTop: theme.spacing.sm,
                  }}
                >
                  <strong>🔧 Tool:</strong> <code>calculator("(9.3 / 10.2) * 100")</code>
                </div>
                <p style={{ marginTop: theme.spacing.sm, fontSize: '0.9em' }}>
                  ✓ Result: 91.2% of total area burned
                </p>
              </div>
            </div>
          </ExpandableSection>

          <p
            style={{
              ...theme.typography.bodyLarge,
              color: theme.colors.secondary,
              marginTop: theme.spacing.lg,
              lineHeight: 1.6,
            }}
          >
            These instructions enable the agent to plan and coordinate tool use, interpret the
            results and determine when enough information has been gathered to answer the user
            request (a process known as{' '}
            <a
              href="https://arxiv.org/pdf/2210.03629"
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: '#1976d2', textDecoration: 'underline' }}
            >
              Reason and Act (ReAct)
            </a>
            ). This allows the agent to perform a sequence of tasks, and then return the final
            result to the user.
          </p>
        </section>
        {/* Final Agent Response Expandable */}
          <ExpandableSection title="Final Agent Response">
            <div
              style={{
                padding: theme.spacing.md,
                backgroundColor: theme.colors.surface,
                borderLeft: `4px solid ${theme.colors.primary}`,
                borderRadius: theme.borderRadius.sm,
              }}
            >
              <p style={{ fontStyle: 'italic', margin: 0, lineHeight: 1.6 }}>
                "The 2025 LA Wildfires severely impacted the Pacific Palisades area. Analysis shows
                9.3 km² of the 10.2 km² area was burned (91.2% of total area). Burn severity
                breakdown: 3.2 km² high severity (31%), 4.1 km² moderate severity (40%), and 2.0
                km² low severity (20%). The visualizations show the dramatic change in vegetation
                health before and after the fire event."
              </p>
            </div>
          </ExpandableSection>

        {/* Architecture Section */}
        <section style={{ marginBottom: theme.spacing.xxl }}>
          <h2
            style={{
              ...theme.typography.headlineMedium,
              marginBottom: theme.spacing.md,
              color: theme.colors.onBackground,
            }}
          >
            Architecture
          </h2>
          <p
            style={{
              ...theme.typography.bodyLarge,
              color: theme.colors.secondary,
              marginBottom: theme.spacing.md,
              lineHeight: 1.6,
            }}
          >
            The application follows a three-tiered architecture comprising the following components:
          </p>
          <ol
            style={{
              ...theme.typography.bodyLarge,
              color: theme.colors.secondary,
              marginBottom: theme.spacing.lg,
              paddingLeft: theme.spacing.lg,
            }}
          >
            <li><strong>React Frontend Layer</strong>: a clean UI for presenting agent reponses and displaying data on a map</li>
            <li><strong>GeoAgent Backend</strong>: LLM-agent orchestrates a multi-step workflowdata for data retrieval and analytics</li>
            <li><strong>Data Layer</strong>: a data layer for accessing geographic data and persisting session-specific assets</li>
          </ol>

          {/* Architecture Diagram */}
          <div
            style={{
              backgroundColor: theme.colors.surface,
              padding: theme.spacing.lg,
              borderRadius: theme.borderRadius.md,
              boxShadow: theme.elevation.level1,
              textAlign: 'center',
              marginBottom: theme.spacing.xxl,
            }}
          >
            <img
              src="/geospatial-agent-on-aws.png"
              alt="Geospatial Agent on AWS Architecture Diagram"
              style={{
                maxWidth: '100%',
                height: 'auto',
                borderRadius: theme.borderRadius.sm,
              }}
            />
          </div>

          <p
            style={{
              ...theme.typography.bodyLarge,
              color: theme.colors.secondary,
              marginBottom: theme.spacing.lg,
              lineHeight: 1.6,
            }}
          >
            Let's dive into the details of each component:
          </p>

          {/* React Frontend Expandable */}
          <ExpandableSection title="React Frontend">
            <p style={{ marginBottom: theme.spacing.md }}>
              The frontend receives streaming agent responses via Server-Sent Events (SSE), parses
              tool calls in real-time, and renders results on an interactive MapLibre map. When the
              agent sends S3 URLs for satellite imagery or analysis results, the frontend requests
              pre-signed URLs (temporary URLs with 1-hour expiration that grant
              access to private S3 objects without exposing AWS credentials). These pre-signed URLs
              are then passed to TiTiler, which converts Cloud Optimized GeoTIFFs into map tiles
              on-the-fly, with bounds optimization ensuring only visible areas are loaded.
            </p>
            <div style={{ marginBottom: theme.spacing.md }}>
              <strong>Core Technologies:</strong>
              <ul style={{ marginTop: theme.spacing.xs, paddingLeft: theme.spacing.lg }}>
                <li>
                  <strong>React 18 + TypeScript:</strong> Type-safe component architecture
                </li>
                <li>
                  <strong>MapLibre GL JS:</strong> Hardware-accelerated WebGL rendering for
                  interactive maps
                </li>
                <li>
                  <strong>Server-Sent Events (SSE):</strong> Real-time streaming of agent responses
                </li>
              </ul>
            </div>
            <div>
              <strong>Key Features:</strong>
              <ul style={{ marginTop: theme.spacing.xs, paddingLeft: theme.spacing.lg }}>
                <li>
                  Interactive mapping with layer management (geometries, satellite imagery, analysis
                  results)
                </li>
                <li>Streaming chat interface with tool call visualization</li>
                <li>Cloud Optimized GeoTIFF (COG) rendering via TiTiler</li>
                <li>
                  Bounds optimization: tiles only load within geometry area (90%+ bandwidth savings)
                </li>
              </ul>
            </div>
          </ExpandableSection>

          {/* GeoAgent Backend Expandable */}
          <ExpandableSection title="GeoAgent Backend">
            <p style={{ marginBottom: theme.spacing.md }}>
              The agent orchestrates a multi-step workflow drawing on utility, geocoding, data retrieval and geospatial analysis tools. For example, it might geocode locations using Amazon
              Location Service (via MCP), fetch precise boundaries from OpenStreetMap, search
              for Sentinel-2 satellite imagery matching the geometry and date, download and clip
              the relevant spectral bands, calculate vegetation/water/burn indices using raster
              operations, save results as Cloud Optimized GeoTIFFs to S3, and stream S3 URLs back
              to the frontend for visualization—all while maintaining conversation context and
              caching prompts for efficiency.
            </p>
            <div style={{ marginBottom: theme.spacing.md }}>
              <strong>Core Framework:</strong>
              <ul style={{ marginTop: theme.spacing.xs, paddingLeft: theme.spacing.lg }}>
                <li>
                  <strong>
                    <a
                      href="https://github.com/awslabs/strands-agents"
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: theme.colors.primary }}
                    >
                      Strands Agents
                    </a>
                    :
                  </strong>{' '}
                  AWS's open-source agentic framework with tool orchestration and streaming
                </li>
                <li>
                  <strong>Amazon Bedrock AgentCore:</strong> Fully-managed, serverless deployment
                  platform for any Framework (Strands, LangChain, LangGraph, CrewAI). We use managed runtime and observability capabilities.
                </li>
                <li>
                  <strong>Claude Sonnet 4.6:</strong> Foundation model with up to 1M context window
                </li>
              </ul>
            </div>
            <div style={{ marginBottom: theme.spacing.md }}>
              <strong>Agent Components:</strong>
              <ol style={{ marginTop: theme.spacing.xs, paddingLeft: theme.spacing.lg }}>
                <li>
                  <strong>Model Context Protocol (MCP):</strong> Custom MCP server for Amazon
                  Location Service geocoding, provides <code>search_places</code> tool
                </li>
                <li>
                  <strong>Conversation Management:</strong> Sliding window (last 3 turns), prompt
                  caching (90% token savings)
                </li>
                <li>
                  <strong>Tools:</strong> Geometry (OSM, bbox, validation), Satellite Data
                  (Sentinel-2), Analysis (NDVI, NDWI, NBR), Utilities (visualization, calculator, etc.)
                </li>
                <li>
                  <strong>Streaming:</strong> Async generator pattern, progressive tool call parsing
                </li>
              </ol>
            </div>
          </ExpandableSection>

          {/* Data Layer Expandable */}
          <ExpandableSection title="Data Layer">
            <p style={{ marginBottom: theme.spacing.md }}>
              The data layer combines multile geographic data sources including: Sentinel-2 satellite imagery from AWS Open Data
              (global coverage, 10-20m resolution), vector data (building data, aministrative boundaries, etc.) from Overture Maps hosted on the AWS Registry of Open Data, OpenStreetMap boundaries via Overpass API
              (precise polygons for named places), and Amazon Location Service for authoritative
              geocoding. All retrieved data and analysis results are stored in S3 with session-based organization and 7-day expiration.
            </p>
            <div style={{ marginBottom: theme.spacing.md }}>
              <strong>Data Sources:</strong>
              <ol style={{ marginTop: theme.spacing.xs, paddingLeft: theme.spacing.lg }}>
                <li>
                  <strong>Sentinel-2 Satellite Imagery (AWS Registry of Open Data):</strong> public at {' '}
                  <a
                    href="https://registry.opendata.aws/sentinel-2/"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: theme.colors.primary }}
                  >
                    https://registry.opendata.aws/sentinel-2/
                  </a>
                  , 10-20m resolution, global coverage, 5-day
                  revisit, bands: Red, Green, NIR, NIR08, SWIR2, TCI
                </li>
                <li>
                  <strong>Overture Maps Foundation Open Map Data (AWS Registry of Open Data):</strong> Public at{' '}
                  <a
                    href="https://registry.opendata.aws/overture/"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: theme.colors.primary }}
                  >
                    https://registry.opendata.aws/overture/
                  </a>
                  , vector data for Admins, Base, Buildings, Places, and Transportation
                </li>
                <li>
                  <strong>OpenStreetMap (OSM) Boundaries:</strong> Overpass API via OSMnx,
                  administrative boundaries, parks, water bodies
                </li>
                <li>
                  <strong>Amazon Location Service:</strong> Authoritative geocoding via MCP server
                </li>
              </ol>
            </div>
            <div>
              <strong>Session Asset Storage (S3):</strong>
              <pre
                style={{
                  backgroundColor: theme.colors.surfaceVariant,
                  padding: theme.spacing.sm,
                  borderRadius: theme.borderRadius.sm,
                  overflow: 'auto',
                  fontSize: '0.85em',
                  marginTop: theme.spacing.xs,
                }}
              >
                {`session_data/{session_id}/
├── geometries/          # OSM polygons, bounding boxes
├── satellite_imagery/   # Sentinel-2 bands (red, green, NIR, SWIR, TCI)
└── analysis_results/    # NDVI, NDWI, NBR maps`}
              </pre>
              <p style={{ marginTop: theme.spacing.sm, fontStyle: 'italic' }}>
                7-day expiration policy for temporary analysis results
              </p>
            </div>
          </ExpandableSection>
        </section>
      </div>
    </div>
  );
}
