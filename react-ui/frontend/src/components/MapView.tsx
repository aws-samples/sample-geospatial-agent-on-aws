import { useEffect, useRef, useState, useMemo } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import MapboxDraw from 'maplibre-gl-draw';
import 'maplibre-gl-draw/dist/mapbox-gl-draw.css';
import { getPresignedUrl } from '../services/api.ts';
import type { GeometryData, RasterData } from '../types.ts';
import { TITILER_URL, TITILER_API_KEY } from '../config.ts';
import {
  formatLayerDisplayText,
  groupLayers,
  type LayerMetadata,
} from '../utils/layerFormatting';

interface MapViewProps {
  geometry: GeometryData | null;
  rasters: RasterData[];
  onDrawnGeometry?: (geojson: any) => void;
}

export function MapView({ geometry, rasters, onDrawnGeometry }: MapViewProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const draw = useRef<MapboxDraw | null>(null);
  const [allLayers, setAllLayers] = useState<LayerMetadata[]>([]); // Persistent layer tracking
  const addedRasterUrls = useRef<Set<string>>(new Set()); // Track added raster URLs to prevent duplicates
  const [rasterVisibility, setRasterVisibility] = useState<Record<string, boolean>>({});
  const [isLayerControlOpen, setIsLayerControlOpen] = useState(true);
  const [drawMode, setDrawMode] = useState<'none' | 'point' | 'polygon'>('none');
  const [hasDrawnFeatures, setHasDrawnFeatures] = useState(false);
  const [baseMapStyle, setBaseMapStyle] = useState<'dark' | 'google-roads' | 'google-satellite' | 'esri-satellite'>('esri-satellite');

  // Memoize layer groups to avoid repeated filtering on each render
  const layerGroups = useMemo(() => groupLayers(allLayers), [allLayers]);

  // Function to update base map layer
  const updateBaseMapLayer = (style: 'dark' | 'google-roads' | 'google-satellite' | 'esri-satellite') => {
    if (!map.current) return;

    const mapInstance = map.current;

    // Remove existing base map layers
    ['dark-base', 'google-roads-base', 'google-satellite-base', 'esri-satellite-base'].forEach(layerId => {
      if (mapInstance.getLayer(layerId)) {
        mapInstance.removeLayer(layerId);
      }
    });

    // Remove existing base map sources
    ['dark-source', 'google-roads-source', 'google-satellite-source', 'esri-satellite-source'].forEach(sourceId => {
      if (mapInstance.getSource(sourceId)) {
        mapInstance.removeSource(sourceId);
      }
    });

    // Get first non-basemap layer for proper ordering
    const layers = mapInstance.getStyle().layers || [];
    const baseLayerIds = ['dark-base', 'google-roads-base', 'google-satellite-base', 'esri-satellite-base'];
    const firstNonBase = layers.find(layer => !baseLayerIds.includes(layer.id));
    const beforeId = firstNonBase?.id;

    // Add new base map layer based on style
    if (style === 'dark') {
      mapInstance.addSource('dark-source', {
        type: 'raster',
        tiles: ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'],
        tileSize: 256,
        attribution: '&copy; OpenStreetMap &copy; CARTO'
      });

      mapInstance.addLayer({
        id: 'dark-base',
        type: 'raster',
        source: 'dark-source',
        minzoom: 0,
        maxzoom: 22
      }, beforeId);
    } else if (style === 'google-roads') {
      mapInstance.addSource('google-roads-source', {
        type: 'raster',
        tiles: ['https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}'],
        tileSize: 256,
        attribution: '&copy; Google Maps'
      });

      mapInstance.addLayer({
        id: 'google-roads-base',
        type: 'raster',
        source: 'google-roads-source',
        minzoom: 0,
        maxzoom: 22
      }, beforeId);
    } else if (style === 'google-satellite') {
      mapInstance.addSource('google-satellite-source', {
        type: 'raster',
        tiles: ['https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}'],
        tileSize: 256,
        attribution: '&copy; Google Maps'
      });

      mapInstance.addLayer({
        id: 'google-satellite-base',
        type: 'raster',
        source: 'google-satellite-source',
        minzoom: 0,
        maxzoom: 22
      }, beforeId);
    } else if (style === 'esri-satellite') {
      mapInstance.addSource('esri-satellite-source', {
        type: 'raster',
        tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
        tileSize: 256,
        attribution: '© Esri'
      });

      mapInstance.addLayer({
        id: 'esri-satellite-base',
        type: 'raster',
        source: 'esri-satellite-source',
        minzoom: 0,
        maxzoom: 22
      }, beforeId);
    }
  };

  // Handle base map style changes
  useEffect(() => {
    if (!map.current || !map.current.loaded()) return;
    updateBaseMapLayer(baseMapStyle);
  }, [baseMapStyle]);

  // Initialize map
  useEffect(() => {
    if (!mapContainer.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: {
        version: 8,
        sources: {},
        layers: []
      },
      center: [0, 20],
      zoom: 2,
      transformRequest: (url) => {
        // Add API key for TiTiler requests
        if (url.startsWith(TITILER_URL) && TITILER_API_KEY) {
          return {
            url: url,
            headers: { 'x-api-key': TITILER_API_KEY }
          };
        }
        return { url };
      },
    });

    // Add global error listener
    map.current.on('error', (e) => {
      console.error('MapLibre error:', e);
    });

    // Load initial basemap (dynamic, controlled by baseMapStyle state)
    map.current.once('load', () => {
      updateBaseMapLayer(baseMapStyle);
    });

    // Initialize draw control
    draw.current = new MapboxDraw({
      displayControlsDefault: false,
      controls: {},
      styles: [
        // Polygon fill
        {
          id: 'gl-draw-polygon-fill',
          type: 'fill',
          filter: ['all', ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']],
          paint: {
            'fill-color': '#FFA500',
            'fill-opacity': 0.3
          }
        },
        // Polygon outline
        {
          id: 'gl-draw-polygon-stroke-active',
          type: 'line',
          filter: ['all', ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']],
          paint: {
            'line-color': '#FFA500',
            'line-width': 3
          }
        },
        // Point
        {
          id: 'gl-draw-point',
          type: 'circle',
          filter: ['all', ['==', '$type', 'Point'], ['!=', 'mode', 'static']],
          paint: {
            'circle-radius': 8,
            'circle-color': '#FFA500'
          }
        },
        // Vertex points
        {
          id: 'gl-draw-polygon-and-line-vertex-active',
          type: 'circle',
          filter: ['all', ['==', 'meta', 'vertex'], ['==', '$type', 'Point']],
          paint: {
            'circle-radius': 5,
            'circle-color': '#FFF'
          }
        }
      ]
    });

    map.current.addControl(draw.current as any);

    // Listen for draw events
    map.current.on('draw.create', () => {
      setHasDrawnFeatures(true);
    });

    map.current.on('draw.delete', () => {
      const data = draw.current?.getAll();
      setHasDrawnFeatures(data ? data.features.length > 0 : false);
    });

    map.current.on('draw.update', () => {
      setHasDrawnFeatures(true);
    });

    return () => {
      map.current?.remove();
    };
  }, []);

  // Add geometry when new geometry is loaded (persistent across turns)
  useEffect(() => {
    console.log(`📍 MapView geometry effect triggered. Geometry:`, geometry ? 'present' : 'null', geometry?.locationName);
    
    if (!map.current || !geometry) return;

    const mapInstance = map.current;

    // Wait for map to load
    const updateGeometry = () => {
      console.log(`📍 Processing geometry:`, geometry.locationName || 'unnamed');
      
      // Generate unique geometry ID with timestamp
      const timestamp = Date.now();
      const geometryId = `geometry-${timestamp}`;
      const fillLayerId = `${geometryId}-fill`;
      const outlineLayerId = `${geometryId}-outline`;
      
      // Check if similar geometry already exists (compare first feature's coordinates)
      const firstFeatureCoords = geometry.features[0]?.geometry?.coordinates;
      const coordsStr = JSON.stringify(firstFeatureCoords);
      const existingGeometry = allLayers.find(l => 
        l.type === 'geometry' && l.url.includes(coordsStr.substring(0, 100))
      );
      
      if (existingGeometry) {
        console.log(`✅ Similar geometry already on map, skipping`);
        return;
      }
      
      console.log(`➕ Adding new geometry to map`);
      
      // Store full geometry for tracking
      const geometryStr = JSON.stringify(geometry.features);

      // Add source with unique ID
      mapInstance.addSource(geometryId, {
        type: 'geojson',
        data: geometry,
      });

      // Add fill layer
      mapInstance.addLayer({
        id: fillLayerId,
        type: 'fill',
        source: geometryId,
        paint: {
          'fill-color': '#088',
          'fill-opacity': 0,
        },
        layout: {
          visibility: 'visible',
        },
      });

      // Add outline layer
      mapInstance.addLayer({
        id: outlineLayerId,
        type: 'line',
        source: geometryId,
        paint: {
          'line-color': '#0FF',
          'line-width': 3,
        },
        layout: {
          visibility: 'visible',
        },
      });

      // Extract layer name from geometry metadata or properties
      let name = geometry.locationName || 'Boundary';
      
      // Fallback to properties if locationName not provided
      if (!geometry.locationName && geometry.features.length > 0 && geometry.features[0].properties) {
        const props = geometry.features[0].properties;
        name = props.name || props.location || props.display_name || 
               props.place_name || props.title || 'Boundary';
      }
      
      // Calculate bounds and flyTo
      const bounds = new maplibregl.LngLatBounds();
      geometry.features.forEach((feature) => {
        const geom = feature.geometry;
        if (geom.type === 'Polygon') {
          geom.coordinates[0].forEach((coord: number[]) => {
            bounds.extend(coord as [number, number]);
          });
        } else if (geom.type === 'MultiPolygon') {
          geom.coordinates.forEach((polygon: number[][][]) => {
            polygon[0].forEach((coord: number[]) => {
              bounds.extend(coord as [number, number]);
            });
          });
        } else if (geom.type === 'Point') {
          bounds.extend(geom.coordinates as [number, number]);
        } else if (geom.type === 'LineString') {
          geom.coordinates.forEach((coord: number[]) => {
            bounds.extend(coord as [number, number]);
          });
        }
      });

      // Convert to [west, south, east, north] format for storage
      const sw = bounds.getSouthWest();
      const ne = bounds.getNorthEast();
      const boundsArray: [number, number, number, number] = [sw.lng, sw.lat, ne.lng, ne.lat];

      // Track this geometry layer with bounds
      setAllLayers(prev => [...prev, {
        id: fillLayerId,
        sourceId: geometryId,
        name: name,
        url: geometryStr,
        type: 'geometry',
        bounds: boundsArray
      }]);
      
      console.log(`✅ Geometry added:`, name);

      mapInstance.fitBounds(bounds, {
        padding: 50,
        duration: 1500,
        maxZoom: 15,
      });
    };

    if (mapInstance.loaded()) {
      updateGeometry();
    } else {
      mapInstance.once('load', updateGeometry);
    }
  }, [geometry, allLayers]);



  // Handle drawing mode changes
  const handleDrawMode = (mode: 'none' | 'point' | 'polygon') => {
    if (!draw.current) return;

    setDrawMode(mode);

    if (mode === 'none') {
      draw.current.changeMode('simple_select');
    } else if (mode === 'point') {
      draw.current.changeMode('draw_point');
    } else if (mode === 'polygon') {
      draw.current.changeMode('draw_polygon');
    }
  };

  // Clear all drawn features
  const clearDrawnFeatures = () => {
    if (!draw.current) return;
    draw.current.deleteAll();
    setHasDrawnFeatures(false);
    setDrawMode('none');
  };

  // Send drawn geometry to chat
  const sendDrawnGeometryToChat = () => {
    if (!draw.current || !onDrawnGeometry) return;

    const data = draw.current.getAll();
    if (data.features.length === 0) return;

    // Send the GeoJSON to the parent component
    onDrawnGeometry(data);

    // Clear the drawn features after sending
    clearDrawnFeatures();
  };

  // Fly to a specific layer's bounds
  const flyToLayer = (layerId: string) => {
    if (!map.current) return;
    
    const layer = allLayers.find(l => l.id === layerId);
    if (!layer || !layer.bounds) return;
    
    const mapInstance = map.current;
    const [west, south, east, north] = layer.bounds;
    
    mapInstance.fitBounds(
      [[west, south], [east, north]],
      {
        padding: 50,
        duration: 1500,
        maxZoom: 15,
      }
    );
    
    console.log(`🎯 Flying to layer:`, layer.name);
  };

  // Remove a specific layer
  const removeLayer = (layerId: string) => {
    if (!map.current) return;

    const layer = allLayers.find(l => l.id === layerId);
    if (!layer) return;

    const mapInstance = map.current;

    if (layer.type === 'geometry') {
      // Geometry has both fill and outline layers
      const outlineLayerId = layer.id.replace('-fill', '-outline');
      if (mapInstance.getLayer(layer.id)) {
        mapInstance.removeLayer(layer.id);
      }
      if (mapInstance.getLayer(outlineLayerId)) {
        mapInstance.removeLayer(outlineLayerId);
      }
      if (mapInstance.getSource(layer.sourceId)) {
        mapInstance.removeSource(layer.sourceId);
      }
    } else {
      // Raster layer
      if (mapInstance.getLayer(layer.id)) {
        mapInstance.removeLayer(layer.id);
      }
      if (mapInstance.getSource(layer.sourceId)) {
        mapInstance.removeSource(layer.sourceId);
      }
      // Remove URL from tracking ref
      addedRasterUrls.current.delete(layer.url);
    }
    
    setAllLayers(prev => prev.filter(l => l.id !== layerId));
    setRasterVisibility(prev => {
      const newVis = { ...prev };
      delete newVis[layerId];
      return newVis;
    });
    
    console.log(`🗑️ Removed layer:`, layer.name);
  };

  // Clear all layers from map (basemap is dynamic and separate)
  const clearAllLayers = () => {
    if (!map.current) return;

    const mapInstance = map.current;
    const count = allLayers.length;

    allLayers.forEach(layer => {
      if (layer.type === 'geometry') {
        // Geometry has both fill and outline layers
        const outlineLayerId = layer.id.replace('-fill', '-outline');
        if (mapInstance.getLayer(layer.id)) {
          mapInstance.removeLayer(layer.id);
        }
        if (mapInstance.getLayer(outlineLayerId)) {
          mapInstance.removeLayer(outlineLayerId);
        }
        if (mapInstance.getSource(layer.sourceId)) {
          mapInstance.removeSource(layer.sourceId);
        }
      } else {
        // Raster layer
        if (mapInstance.getLayer(layer.id)) {
          mapInstance.removeLayer(layer.id);
        }
        if (mapInstance.getSource(layer.sourceId)) {
          mapInstance.removeSource(layer.sourceId);
        }
      }
    });

    // Clear all layers (basemap is dynamic and not tracked here)
    setAllLayers([]);
    setRasterVisibility({});
    addedRasterUrls.current.clear(); // Clear the URL tracking ref

    console.log(`🗑️ Cleared ${count} layers`);
  };

  // Add COG raster layers when raster data is available
  useEffect(() => {
    console.log(`🗺️ MapView raster effect triggered. Rasters:`, rasters.length, rasters.map(r => ({ name: r.name, url: r.url.substring(0, 80) })));

    if (!map.current) {
      console.log(`⚠️ Map not initialized yet`);
      return;
    }

    const mapInstance = map.current;

    // Create an abort controller for this effect run
    const abortController = new AbortController();

    const updateRasters = async () => {
      console.log(`🔧 updateRasters() called for ${rasters.length} rasters`);

      if (abortController.signal.aborted) {
        console.log(`⏭️ Update aborted (new rasters arrived)`);
        return;
      }

      // If no rasters, just return
      if (rasters.length === 0) return;

      // Deduplicate rasters by URL within this batch
      const uniqueRasters = rasters.filter((raster, index, self) =>
        index === self.findIndex(r => r.url === raster.url)
      );
      console.log(`🔍 After deduplication: ${uniqueRasters.length} unique rasters`);

      // Filter to .tif files only and sort by zIndex (higher = on top)
      const rastersToAdd = uniqueRasters
        .filter(r => r.url.toLowerCase().endsWith('.tif'))
        .sort((a, b) => {
          // Both have zIndex: sort by zIndex (higher first)
          if (a.zIndex !== undefined && b.zIndex !== undefined) {
            return b.zIndex - a.zIndex;
          }
          // Only a has zIndex: a comes first
          if (a.zIndex !== undefined) return -1;
          // Only b has zIndex: b comes first
          if (b.zIndex !== undefined) return 1;
          // Neither has zIndex: maintain original order
          return 0;
        });
      console.log(`🔍 After .tif filter and zIndex sort: ${rastersToAdd.length} rasters to add:`, rastersToAdd.map(r => `${r.name} (z:${r.zIndex || 'none'})`));

      if (rastersToAdd.length === 0) {
        console.log(`✅ No rasters to add (no .tif files in batch)`);
        return;
      }

      console.log(`📊 Starting to add ${rastersToAdd.length} rasters to map`);

      // Calculate bounds from geometry once (used for all rasters)
      let bounds: [number, number, number, number] | undefined;
      if (geometry) {
        const boundsObj = new maplibregl.LngLatBounds();
        geometry.features.forEach((feature) => {
          const geom = feature.geometry;
          if (geom.type === 'Polygon') {
            geom.coordinates[0].forEach((coord: number[]) => {
              boundsObj.extend(coord as [number, number]);
            });
          } else if (geom.type === 'MultiPolygon') {
            geom.coordinates.forEach((polygon: number[][][]) => {
              polygon[0].forEach((coord: number[]) => {
                boundsObj.extend(coord as [number, number]);
              });
            });
          } else if (geom.type === 'Point') {
            boundsObj.extend(geom.coordinates as [number, number]);
          } else if (geom.type === 'LineString') {
            geom.coordinates.forEach((coord: number[]) => {
              boundsObj.extend(coord as [number, number]);
            });
          }
        });

        // Convert to [west, south, east, north] format
        const sw = boundsObj.getSouthWest();
        const ne = boundsObj.getNorthEast();
        bounds = [sw.lng, sw.lat, ne.lng, ne.lat];
      }

      // Process in order (zIndex already sorted, higher first)
      // With beforeId logic, first added = top visually
      const newLayerMetadata: LayerMetadata[] = [];

      // Generate base timestamp once for this batch to ensure uniqueness
      const baseTimestamp = Date.now();

      for (let i = 0; i < rastersToAdd.length; i++) {
        // Check for abort at the beginning of each iteration
        if (abortController.signal.aborted) {
          console.log(`⏭️ [${i}] Loop aborted by new rasters arriving`);
          break;
        }

        const raster = rastersToAdd[i];

        // Check if this raster URL has already been added using ref (avoids stale closure)
        if (addedRasterUrls.current.has(raster.url)) {
          console.log(`⏭️ [${i}] Raster already on map, skipping:`, raster.name, `URL:`, raster.url);
          continue;
        }

        console.log(`✅ [${i}] Raster NOT in ref, will add:`, raster.name, `URL:`, raster.url);
        console.log(`📋 Current ref contents:`, Array.from(addedRasterUrls.current));

        // Use base timestamp + index for unique IDs across conversation turns
        const layerId = `cogLayer-raster-${baseTimestamp}-${i}`;
        const sourceId = `cogSource-raster-${baseTimestamp}-${i}`;

        console.log(`🔄 [${i}] Starting to add raster:`, raster.name, `URL:`, raster.url.substring(0, 80));

        try {
          // Check for abort before async operation
          if (abortController.signal.aborted) {
            console.log(`⏭️ [${i}] Aborted before getPresignedUrl for:`, raster.name);
            break;
          }

          console.log(`🔄 [${i}] Getting presigned URL for:`, raster.name);
          const presignedUrl = await getPresignedUrl(raster.url);

          // Check for abort after async operation
          if (abortController.signal.aborted) {
            console.log(`⏭️ [${i}] Aborted after getPresignedUrl for:`, raster.name);
            break;
          }

          console.log(`✅ [${i}] Pre-signed URL generated for:`, raster.name);
          if (!presignedUrl) {
            console.error(`❌ [${i}] Pre-signed URL failed:`, raster.name, raster.url);
            continue;
          }

          const encodedUrl = encodeURIComponent(presignedUrl);

          // Detect raster type from URL and apply appropriate colormap
          const urlLower = raster.url.toLowerCase();
          let tileUrl: string;

          if (urlLower.includes('ndvi_') || urlLower.includes('ndvi-')) {
            // NDVI: 0 to 1 range, green colormap for vegetation
            tileUrl = `${TITILER_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=${encodedUrl}&bidx=1&rescale=0,1&colormap_name=rdylgn`;
          } else if (urlLower.includes('ndwi_') || urlLower.includes('ndwi-')) {
            // NDWI: -1 to 1, blue colormap for water
            tileUrl = `${TITILER_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=${encodedUrl}&bidx=1&rescale=-1,1&colormap_name=blues`;
          } else if (urlLower.includes('nbr_') || urlLower.includes('nbr-') || urlLower.includes('/nbr.')) {
            // NBR: -1 to 1, spectral colormap for burn severity
            tileUrl = `${TITILER_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=${encodedUrl}&bidx=1&rescale=-1,1&colormap_name=spectral`;
          } else {
            // TCI and other satellite images: no colormap needed (RGB)
            tileUrl = `${TITILER_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=${encodedUrl}`;
          }

          // Check for abort before adding to map
          if (abortController.signal.aborted) {
            console.log(`⏭️ [${i}] Aborted before adding source/layer for:`, raster.name);
            break;
          }

          mapInstance.addSource(sourceId, {
            type: 'raster',
            tiles: [tileUrl],
            tileSize: 256,
            minzoom: 0,
            maxzoom: 22,
            bounds: bounds,
          });

          const isVisible = rasterVisibility[layerId] !== undefined ? rasterVisibility[layerId] : true;
          setRasterVisibility(prev => ({ ...prev, [layerId]: isVisible }));

          // Find the first non-basemap raster layer to insert before it
          // This ensures COG layers are added above basemaps but maintain their own zIndex order
          const mapLayers = mapInstance.getStyle().layers || [];
          const basemapLayerIds = ['dark-base', 'google-roads-base', 'google-satellite-base', 'esri-satellite-base'];
          const firstNonBasemapRaster = mapLayers.find((l: any) =>
            l.type === 'raster' &&
            !basemapLayerIds.includes(l.id)
          );
          const beforeId = firstNonBasemapRaster ? firstNonBasemapRaster.id : undefined;

          // Add raster layer (above basemaps, beforeId places new layers at bottom of COG stack)
          mapInstance.addLayer({
            id: layerId,
            type: 'raster',
            source: sourceId,
            paint: {
              'raster-opacity': 1.0,
            },
            layout: {
              visibility: isVisible ? 'visible' : 'none',
            },
          }, beforeId);

          // Track this layer with metadata
          newLayerMetadata.push({
            id: layerId,
            sourceId: sourceId,
            name: raster.name,
            url: raster.url,
            date: raster.date,
            type: 'raster',
            bounds: bounds
          });

          // CRITICAL FIX: Only add URL to ref AFTER layer is successfully added to map
          // This prevents race condition where ref is updated but layer isn't added due to abort
          addedRasterUrls.current.add(raster.url);

          console.log(`✅ [${i}] Raster successfully added to map:`, raster.name, `Layer ID: ${layerId}`, `Visible: ${isVisible}`);

          // Add error listener for tile loading
          mapInstance.on('error', (e: any) => {
            if (e.sourceId === sourceId) {
              console.error(`❌ Tile loading error for ${raster.name}:`, e);
            }
          });
        } catch (error) {
          console.error(`❌ [${i}] Raster layer error for ${raster.name}:`, error);
          // Don't add to ref if there was an error
        }
      }
      
      console.log(`🏁 Finished processing loop. Added ${newLayerMetadata.length} layers to metadata`);

      // Update layer tracking with new layers (keep same order as zIndex)
      if (newLayerMetadata.length > 0) {
        console.log(`📋 Updating allLayers state with ${newLayerMetadata.length} new layers:`, newLayerMetadata.map(l => l.name));
        setAllLayers(prev => {
          const updated = [...prev, ...newLayerMetadata];
          console.log(`📋 Total layers after update: ${updated.length}`);
          return updated;
        });
      }
    };

    // Always call updateRasters directly - the map exists and is initialized
    // Note: mapInstance.loaded() can return false during layer operations even when map is ready
    console.log(`✅ Calling updateRasters()`);
    updateRasters();
    
    // Cleanup function to abort if new rasters arrive
    return () => {
      abortController.abort();
    };
  }, [rasters]); // Only depend on rasters - addedRasterUrls ref is always current

  // Handle layer visibility toggles (both rasters and geometries)
  useEffect(() => {
    if (!map.current) return;

    const mapInstance = map.current;

    // Update visibility for each layer
    Object.entries(rasterVisibility).forEach(([layerId, isVisible]) => {
      const visibility = isVisible ? 'visible' : 'none';
      
      // Update the main layer
      if (mapInstance.getLayer(layerId)) {
        mapInstance.setLayoutProperty(layerId, 'visibility', visibility);
      }
      
      // For geometry layers, also update the outline layer
      if (layerId.includes('geometry') && layerId.includes('-fill')) {
        const outlineLayerId = layerId.replace('-fill', '-outline');
        if (mapInstance.getLayer(outlineLayerId)) {
          mapInstance.setLayoutProperty(outlineLayerId, 'visibility', visibility);
        }
      }
    });
  }, [rasterVisibility]);

  // Clear all layers when reset is triggered (geometry and rasters both become null/empty)
  useEffect(() => {
    if (!geometry && rasters.length === 0 && allLayers.length > 0) {
      console.log('🔄 Reset detected - clearing all layers');
      clearAllLayers();
    }
  }, [geometry, rasters, allLayers]);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div
        ref={mapContainer}
        style={{
          width: '100%',
          height: '100%',
          minHeight: '600px',
        }}
      />

      {/* Layer control panel */}
      {allLayers.length > 0 && (
        <div
          style={{
            position: 'absolute',
            top: '16px',
            right: '16px',
            backgroundColor: '#FFFFFF',
            borderRadius: '8px',
            boxShadow: '0 4px 8px rgba(0,0,0,0.16)',
            zIndex: 1,
            maxHeight: '80vh',
            overflowY: 'auto',
            fontFamily: 'Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          }}
        >
          {/* Header with toggle and clear button */}
          <div style={{ display: 'flex', alignItems: 'center', borderBottom: '1px solid #E0E0E0' }}>
            <button
              onClick={() => setIsLayerControlOpen(!isLayerControlOpen)}
              style={{
                flex: 1,
                padding: '16px',
                backgroundColor: 'transparent',
                border: 'none',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                fontWeight: 500,
                fontSize: '16px',
                color: '#1C1B1F',
                transition: 'background-color 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'rgba(0, 0, 0, 0.04)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent';
              }}
            >
              <span>Layers ({allLayers.length})</span>
              <span style={{ fontSize: '12px', color: '#424242' }}>
                {isLayerControlOpen ? '▼' : '▶'}
              </span>
            </button>
            {allLayers.length > 0 && (
              <button
                onClick={clearAllLayers}
                style={{
                  padding: '12px 16px',
                  backgroundColor: '#000000',
                  color: '#FFFFFF',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontSize: '14px',
                  fontWeight: 500,
                  marginRight: '8px',
                  transition: 'all 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = '#424242';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = '#000000';
                }}
                title="Clear all layers"
              >
                Clear
              </button>
            )}
          </div>

            {/* Layer controls (collapsible) */}
            {isLayerControlOpen && (
              <div
                style={{
                  padding: '0 12px 12px 12px',
                  minWidth: '250px',
                }}
              >
                {/* Satellite Images (TCI) - All rasters except basemap and spectral indices */}
                {layerGroups.tci.length > 0 && (
                  <div style={{ marginBottom: '16px' }}>
                    <div style={{ fontWeight: 500, fontSize: '14px', marginBottom: '8px', color: '#424242', marginTop: '12px' }}>
                      Satellite Images (TCI)
                    </div>
                    {layerGroups.tci.map((layer) => {
                      const isVisible = rasterVisibility[layer.id] !== false;
                      const displayText = formatLayerDisplayText(layer, 'tci');

                      return (
                        <div
                          key={layer.id}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '6px',
                            fontSize: '12px',
                            marginBottom: '6px',
                            paddingLeft: '6px',
                            padding: '6px 8px',
                            borderRadius: '4px',
                            transition: 'background-color 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.backgroundColor = 'rgba(0, 0, 0, 0.04)';
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.backgroundColor = 'transparent';
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={isVisible}
                            onChange={(e) => {
                              e.stopPropagation();
                              setRasterVisibility(prev => ({ ...prev, [layer.id]: e.target.checked }));
                            }}
                            style={{ cursor: 'pointer', width: '18px', height: '18px' }}
                          />
                          <span 
                            style={{ flex: 1, fontWeight: 400, cursor: 'pointer', color: '#1C1B1F' }} 
                            onClick={() => flyToLayer(layer.id)}
                            title="Click to zoom to this layer"
                          >
                            {displayText}
                          </span>
                          <button
                            onClick={() => removeLayer(layer.id)}
                            style={{
                              padding: '4px 8px',
                              backgroundColor: '#000000',
                              color: '#FFFFFF',
                              border: 'none',
                              borderRadius: '4px',
                              cursor: 'pointer',
                              fontSize: '12px',
                              fontWeight: 500,
                              transition: 'background-color 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.backgroundColor = '#424242';
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.backgroundColor = '#000000';
                            }}
                            title="Remove layer"
                          >
                            ×
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Spectral Indices (NDVI, NBR, NDWI) */}
                {layerGroups.spectralIndices.length > 0 && (
                  <div style={{ marginBottom: '16px' }}>
                    <div style={{ fontWeight: 500, fontSize: '14px', marginBottom: '8px', color: '#424242' }}>
                      Spectral Indices
                    </div>
                    {layerGroups.spectralIndices.map((layer) => {
                      const isVisible = rasterVisibility[layer.id] !== false;
                      const displayText = formatLayerDisplayText(layer, 'spectral');

                      return (
                        <div
                          key={layer.id}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '6px',
                            fontSize: '12px',
                            marginBottom: '6px',
                            paddingLeft: '6px',
                            padding: '6px 8px',
                            borderRadius: '4px',
                            transition: 'background-color 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.backgroundColor = 'rgba(0, 0, 0, 0.04)';
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.backgroundColor = 'transparent';
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={isVisible}
                            onChange={(e) => {
                              e.stopPropagation();
                              setRasterVisibility(prev => ({ ...prev, [layer.id]: e.target.checked }));
                            }}
                            style={{ cursor: 'pointer', width: '18px', height: '18px' }}
                          />
                          <span
                            style={{ flex: 1, fontWeight: 400, cursor: 'pointer', color: '#1C1B1F' }}
                            onClick={() => flyToLayer(layer.id)}
                            title="Click to zoom to this layer"
                          >
                            {displayText}
                          </span>
                          <button
                            onClick={() => removeLayer(layer.id)}
                            style={{
                              padding: '4px 8px',
                              backgroundColor: '#000000',
                              color: '#FFFFFF',
                              border: 'none',
                              borderRadius: '4px',
                              cursor: 'pointer',
                              fontSize: '12px',
                              fontWeight: 500,
                              transition: 'background-color 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.backgroundColor = '#424242';
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.backgroundColor = '#000000';
                            }}
                            title="Remove layer"
                          >
                            ×
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Geometry Layers */}
                {layerGroups.geometries.length > 0 && (
                  <div>
                    <div style={{ fontWeight: 500, fontSize: '14px', marginBottom: '8px', color: '#424242' }}>
                      Geometries
                    </div>
                    {layerGroups.geometries.map((layer) => {
                      const isVisible = rasterVisibility[layer.id] !== false;

                      return (
                        <div
                          key={layer.id}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '6px',
                            fontSize: '12px',
                            marginBottom: '6px',
                            padding: '8px',
                            borderRadius: '4px',
                            transition: 'background-color 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.backgroundColor = 'rgba(0, 0, 0, 0.04)';
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.backgroundColor = 'transparent';
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={isVisible}
                            onChange={(e) => {
                              e.stopPropagation();
                              setRasterVisibility(prev => ({ ...prev, [layer.id]: e.target.checked }));
                            }}
                            style={{ cursor: 'pointer', width: '18px', height: '18px' }}
                          />
                          <span 
                            style={{ flex: 1, fontWeight: 400, cursor: 'pointer', color: '#1C1B1F' }} 
                            onClick={() => flyToLayer(layer.id)}
                            title="Click to zoom to this layer"
                          >
                            {layer.name}
                          </span>
                          <button
                            onClick={() => removeLayer(layer.id)}
                            style={{
                              padding: '4px 8px',
                              backgroundColor: '#000000',
                              color: '#FFFFFF',
                              border: 'none',
                              borderRadius: '4px',
                              cursor: 'pointer',
                              fontSize: '12px',
                              fontWeight: 500,
                              transition: 'background-color 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.backgroundColor = '#424242';
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.backgroundColor = '#000000';
                            }}
                            title="Remove layer"
                          >
                            ×
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
        </div>
      )}

      {/* Drawing controls panel */}
      <div
        style={{
          position: 'absolute',
          top: '16px',
          left: '16px',
          backgroundColor: '#FFFFFF',
          borderRadius: '8px',
          boxShadow: '0 4px 8px rgba(0,0,0,0.16)',
          zIndex: 1,
          fontFamily: 'Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          padding: '10px',
          minWidth: '160px',
        }}
      >
        <div style={{ fontWeight: 500, fontSize: '14px', marginBottom: '8px', color: '#1C1B1F', textAlign: 'center' }}>
          Draw
        </div>

        {/* Drawing mode buttons */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: hasDrawnFeatures ? '8px' : '0' }}>
          <button
            onClick={() => handleDrawMode('point')}
            style={{
              padding: '8px 12px',
              backgroundColor: drawMode === 'point' ? '#FFA500' : '#F5F5F5',
              color: drawMode === 'point' ? '#FFFFFF' : '#1C1B1F',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '13px',
              fontWeight: 500,
              transition: 'all 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
            }}
            onMouseEnter={(e) => {
              if (drawMode !== 'point') {
                e.currentTarget.style.backgroundColor = '#E0E0E0';
              }
            }}
            onMouseLeave={(e) => {
              if (drawMode !== 'point') {
                e.currentTarget.style.backgroundColor = '#F5F5F5';
              }
            }}
          >
            📍 Point
          </button>

          <button
            onClick={() => handleDrawMode('polygon')}
            style={{
              padding: '8px 12px',
              backgroundColor: drawMode === 'polygon' ? '#FFA500' : '#F5F5F5',
              color: drawMode === 'polygon' ? '#FFFFFF' : '#1C1B1F',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '13px',
              fontWeight: 500,
              transition: 'all 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
            }}
            onMouseEnter={(e) => {
              if (drawMode !== 'polygon') {
                e.currentTarget.style.backgroundColor = '#E0E0E0';
              }
            }}
            onMouseLeave={(e) => {
              if (drawMode !== 'polygon') {
                e.currentTarget.style.backgroundColor = '#F5F5F5';
              }
            }}
          >
            ⬟ Polygon
          </button>

          {drawMode !== 'none' && (
            <button
              onClick={() => handleDrawMode('none')}
              style={{
                padding: '6px 12px',
                backgroundColor: '#F5F5F5',
                color: '#1C1B1F',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
                fontSize: '12px',
                fontWeight: 400,
                transition: 'all 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = '#E0E0E0';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = '#F5F5F5';
              }}
            >
              Cancel
            </button>
          )}
        </div>

        {/* Action buttons */}
        {hasDrawnFeatures && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', borderTop: '1px solid #E0E0E0', paddingTop: '8px' }}>
            <button
              onClick={sendDrawnGeometryToChat}
              disabled={!onDrawnGeometry}
              style={{
                padding: '8px 12px',
                backgroundColor: onDrawnGeometry ? '#000000' : '#CCCCCC',
                color: '#FFFFFF',
                border: 'none',
                borderRadius: '4px',
                cursor: onDrawnGeometry ? 'pointer' : 'not-allowed',
                fontSize: '13px',
                fontWeight: 500,
                transition: 'all 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
              }}
              onMouseEnter={(e) => {
                if (onDrawnGeometry) {
                  e.currentTarget.style.backgroundColor = '#424242';
                }
              }}
              onMouseLeave={(e) => {
                if (onDrawnGeometry) {
                  e.currentTarget.style.backgroundColor = '#000000';
                }
              }}
            >
              ✉️ Send
            </button>

            <button
              onClick={clearDrawnFeatures}
              style={{
                padding: '6px 12px',
                backgroundColor: '#F5F5F5',
                color: '#D32F2F',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
                fontSize: '12px',
                fontWeight: 400,
                transition: 'all 200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = '#FFEBEE';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = '#F5F5F5';
              }}
            >
              🗑️ Clear
            </button>
          </div>
        )}
      </div>

      {/* Basemap Switcher - Bottom Right */}
      <div
        style={{
          position: 'absolute',
          bottom: '40px',
          right: '16px',
          backgroundColor: '#FFFFFF',
          borderRadius: '6px',
          boxShadow: '0 2px 6px rgba(0,0,0,0.15)',
          zIndex: 1,
          fontFamily: 'Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        }}
      >
        <select
          value={baseMapStyle}
          onChange={(e) => setBaseMapStyle(e.target.value as 'dark' | 'google-roads' | 'google-satellite' | 'esri-satellite')}
          style={{
            padding: '6px 10px',
            fontSize: '12px',
            fontWeight: 500,
            color: '#1C1B1F',
            backgroundColor: '#FFFFFF',
            border: 'none',
            borderRadius: '6px',
            cursor: 'pointer',
            outline: 'none',
            fontFamily: 'inherit',
          }}
        >
          <option value="dark">Dark</option>
          <option value="google-roads">Roads</option>
          <option value="google-satellite">Google Satellite</option>
          <option value="esri-satellite">Esri Satellite</option>
        </select>
      </div>
    </div>
  );
}
