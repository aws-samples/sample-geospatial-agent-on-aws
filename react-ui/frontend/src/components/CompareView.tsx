import { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import { getPresignedUrl } from '../services/api.ts';
import { TITILER_URL, TITILER_API_KEY } from '../config.ts';
import type { LayerMetadata } from '../utils/layerFormatting';

// Load the Compare class lazily to avoid ES module hoisting issues.
// The dist bundle attaches Compare to window.maplibregl, but static imports
// are hoisted above our window assignment. Dynamic import solves this.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let CompareClass: any = null;
async function loadCompare() {
  if (CompareClass) return CompareClass;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (window as any).maplibregl = maplibregl;
  await import('@maplibre/maplibre-gl-compare/dist/maplibre-gl-compare.js');
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  CompareClass = (window as any).maplibregl?.Compare;
  return CompareClass;
}

interface CompareViewProps {
  leftLayer: LayerMetadata;
  rightLayer: LayerMetadata;
  bounds?: [number, number, number, number];
  center?: [number, number];
  zoom?: number;
  onClose: () => void;
}

// SVG for the swiper handle (two horizontal arrows)
const SWIPER_SVG = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='60' height='60' viewBox='0 0 60 60'%3E%3Cpolygon fill='%23fff' points='20,30 28,24 28,36'/%3E%3Cpolygon fill='%23fff' points='40,30 32,24 32,36'/%3E%3C/svg%3E")`;

export function CompareView({ leftLayer, rightLayer, bounds, center, zoom, onClose }: CompareViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const leftMapRef = useRef<HTMLDivElement>(null);
  const rightMapRef = useRef<HTMLDivElement>(null);
  const leftMap = useRef<maplibregl.Map | null>(null);
  const rightMap = useRef<maplibregl.Map | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const compareRef = useRef<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!containerRef.current || !leftMapRef.current || !rightMapRef.current) return;

    let cancelled = false;

    async function init() {
      const basemapTiles = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';

      const mapOptions: Partial<maplibregl.MapOptions> = {
        style: {
          version: 8 as const,
          sources: {
            'basemap': {
              type: 'raster',
              tiles: [basemapTiles],
              tileSize: 256,
            },
          },
          layers: [{
            id: 'basemap',
            type: 'raster',
            source: 'basemap',
          }],
        },
        center: center || [0, 20],
        zoom: zoom || 2,
        transformRequest: (url) => {
          if (TITILER_API_KEY && url.includes(new URL(TITILER_URL).hostname)) {
            return { url, headers: { 'x-api-key': TITILER_API_KEY } };
          }
          return { url };
        },
      };

      // Create both maps
      const lMap = new maplibregl.Map({
        ...mapOptions,
        container: leftMapRef.current!,
      });

      const rMap = new maplibregl.Map({
        ...mapOptions,
        container: rightMapRef.current!,
      });

      leftMap.current = lMap;
      rightMap.current = rMap;

      // Wait for both maps to load
      await Promise.all([
        new Promise<void>(resolve => lMap.on('load', resolve)),
        new Promise<void>(resolve => rMap.on('load', resolve)),
      ]);

      if (cancelled) return;

      // Add raster layers to each map
      async function addTileLayer(mapInstance: maplibregl.Map, layer: LayerMetadata) {
        const presignedUrl = await getPresignedUrl(layer.url);
        if (!presignedUrl) return;

        const encodedUrl = encodeURIComponent(presignedUrl);
        const urlLower = layer.url.toLowerCase();
        let tileUrl: string;

        if (urlLower.includes('change_detection') || urlLower.includes('change-detection')) {
          tileUrl = `${TITILER_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=${encodedUrl}&bidx=1&rescale=0,0.5&colormap_name=rdylgn_r`;
        } else if (urlLower.includes('ndvi_') || urlLower.includes('ndvi-')) {
          tileUrl = `${TITILER_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=${encodedUrl}&bidx=1&rescale=0,1&colormap_name=rdylgn`;
        } else if (urlLower.includes('ndwi_') || urlLower.includes('ndwi-')) {
          tileUrl = `${TITILER_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=${encodedUrl}&bidx=1&rescale=-1,1&colormap_name=blues`;
        } else if (urlLower.includes('nbr_') || urlLower.includes('nbr-') || urlLower.includes('/nbr.')) {
          tileUrl = `${TITILER_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=${encodedUrl}&bidx=1&rescale=-1,1&colormap_name=spectral`;
        } else {
          tileUrl = `${TITILER_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=${encodedUrl}`;
        }

        // Calculate appropriate minzoom from bounds
        let sourceMinZoom = 0;
        if (bounds) {
          const lonExtent = bounds[2] - bounds[0];
          for (let z = 0; z <= 18; z++) {
            const tileLonExtent = 360 / Math.pow(2, z);
            if (lonExtent / tileLonExtent > 0.1) {
              sourceMinZoom = z;
              break;
            }
          }
        }

        mapInstance.addSource('compare-layer', {
          type: 'raster',
          tiles: [tileUrl],
          tileSize: 256,
          minzoom: sourceMinZoom,
          maxzoom: 22,
          bounds: bounds, // Constrain tile requests to raster extent
        });

        mapInstance.addLayer({
          id: 'compare-layer',
          type: 'raster',
          source: 'compare-layer',
          paint: { 'raster-opacity': 1.0 },
        });
      }

      await Promise.all([
        addTileLayer(lMap, leftLayer),
        addTileLayer(rMap, rightLayer),
      ]);

      if (cancelled) return;

      // Initialize the compare control
      const Cmp = await loadCompare();
      if (Cmp) {
        compareRef.current = new Cmp(lMap, rMap, containerRef.current!, {});
      }

      // Fit to bounds if provided, otherwise the maps already start at the correct center/zoom
      if (bounds) {
        lMap.fitBounds(bounds, { padding: 40, animate: false });
        rMap.fitBounds(bounds, { padding: 40, animate: false });
      }

      // Wait for both maps to finish rendering tiles before hiding loading overlay
      const waitForIdle = (m: maplibregl.Map) =>
        new Promise<void>(resolve => {
          if (m.isSourceLoaded('compare-layer')) {
            resolve();
          } else {
            m.once('idle', () => resolve());
          }
        });

      await Promise.all([waitForIdle(lMap), waitForIdle(rMap)]);
      if (!cancelled) setLoading(false);
    }

    init();

    return () => {
      cancelled = true;
      if (compareRef.current) {
        compareRef.current.remove();
        compareRef.current = null;
      }
      if (leftMap.current) {
        leftMap.current.remove();
        leftMap.current = null;
      }
      if (rightMap.current) {
        rightMap.current.remove();
        rightMap.current = null;
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leftLayer, rightLayer, bounds]);

  const leftDate = leftLayer.date || 'Before';
  const rightDate = rightLayer.date || 'After';

  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 10, backgroundColor: '#000' }}>
      {/* Inline compare slider styles */}
      <style>{`
        .maplibregl-compare {
          background-color: #fff;
          position: absolute;
          width: 2px;
          height: 100%;
          z-index: 1;
        }
        .maplibregl-compare .compare-swiper-vertical {
          background-color: #3887be;
          box-shadow: inset 0 0 0 2px #fff;
          display: inline-block;
          border-radius: 50%;
          position: absolute;
          width: 60px;
          height: 60px;
          top: 50%;
          left: -30px;
          margin: -30px 1px 0;
          color: #fff;
          cursor: ew-resize;
          background-image: ${SWIPER_SVG};
          background-size: 60px;
          background-repeat: no-repeat;
        }
      `}</style>
      {/* Header bar */}
      <div style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 20,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '8px 16px',
        backgroundColor: 'rgba(0,0,0,0.7)',
        backdropFilter: 'blur(8px)',
        color: '#fff',
        fontFamily: 'Roboto, sans-serif',
      }}>
        <span style={{ fontSize: '14px', fontWeight: 500 }}>
          {leftDate}
        </span>
        <button
          onClick={onClose}
          style={{
            padding: '6px 16px',
            backgroundColor: '#fff',
            color: '#000',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '13px',
            fontWeight: 500,
          }}
        >
          Close Compare
        </button>
        <span style={{ fontSize: '14px', fontWeight: 500 }}>
          {rightDate}
        </span>
      </div>

      {/* Loading indicator */}
      {loading && (
        <div style={{
          position: 'absolute',
          inset: 0,
          zIndex: 15,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: 'rgba(0,0,0,0.5)',
          color: '#fff',
          fontSize: '16px',
        }}>
          Loading comparison...
        </div>
      )}

      {/* Compare container with two map divs */}
      <div ref={containerRef} style={{ position: 'absolute', inset: 0 }}>
        <div ref={leftMapRef} style={{ position: 'absolute', inset: 0 }} />
        <div ref={rightMapRef} style={{ position: 'absolute', inset: 0 }} />
      </div>
    </div>
  );
}
