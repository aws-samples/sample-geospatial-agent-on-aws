/**
 * Utility functions for formatting and categorizing map layers
 */

export interface LayerMetadata {
  id: string;
  sourceId: string;
  name: string;
  url: string;
  date?: string;
  type: 'raster' | 'geometry';
  bounds?: [number, number, number, number]; // [west, south, east, north]
}

// Constants
export const BASEMAP_LAYER_ID = 'esri-world-imagery-layer';

/**
 * Format date to short format (Jan 06 2025)
 */
export function formatDate(date: string): string {
  if (!date) return '';

  try {
    const dateObj = new Date(date);
    // Check if date is valid
    if (isNaN(dateObj.getTime())) {
      return date; // Return original if invalid
    }

    const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const month = monthNames[dateObj.getMonth()];
    const day = String(dateObj.getDate()).padStart(2, '0');
    const year = dateObj.getFullYear();
    return `${month} ${day} ${year}`;
  } catch (e) {
    return date; // Fallback to original if parsing fails
  }
}

/**
 * Extract location and clean up layer name from tool response
 */
export function cleanLayerName(name: string): string {
  // Extract location from patterns like "Satellite Image of Hyde Park, London - October 6, 2025"
  const ofMatch = name.match(/of\s+([^-]+?)(?:\s+-|$)/i);
  if (ofMatch) {
    return ofMatch[1].trim();
  }

  // Remove common verbose prefixes and suffixes
  let cleaned = name
    .replace(/^Satellite Image of\s+/i, '')
    .replace(/^True Color Image of\s+/i, '')
    .replace(/^TCI of\s+/i, '')
    .replace(/^NDVI Vegetation Map of\s+/i, '')
    .replace(/^NBR Burn Severity Map of\s+/i, '')
    .replace(/^NDWI Water Map of\s+/i, '')
    .replace(/\s+-\s+\w+\s+\d+,\s+\d{4}.*$/i, '') // Remove verbose dates at end
    .trim();
  
  // Remove redundant suffixes like "NDVI - Vegetation Status", "NBR - Burn Severity"
  cleaned = cleaned
    .replace(/\s+NDVI\s+-\s+.*$/i, ' NDVI')
    .replace(/\s+NBR\s+-\s+.*$/i, ' NBR')
    .replace(/\s+NDWI\s+-\s+.*$/i, ' NDWI')
    .trim();
  
  return cleaned;
}

/**
 * Map technical spectral index names to friendly names
 */
export function getFriendlyIndexName(name: string): string {
  const nameLower = name.toLowerCase();
  if (nameLower.includes('ndvi')) {
    return 'Vegetation Index';
  } else if (nameLower.includes('nbr')) {
    return 'Burn Rate';
  } else if (nameLower.includes('ndwi')) {
    return 'Water Index';
  }
  return name; // Fallback to original name
}

/**
 * Check if a layer is a spectral index (NDVI, NBR, NDWI)
 */
export function isSpectralIndex(layer: LayerMetadata): boolean {
  const nameLower = layer.name.toLowerCase();
  return nameLower.includes('ndvi') ||
         nameLower.includes('nbr') ||
         nameLower.includes('ndwi');
}

/**
 * Format layer display text based on layer type
 */
export function formatLayerDisplayText(layer: LayerMetadata, layerType: 'tci' | 'spectral' | 'geometry'): string {
  const cleanedName = cleanLayerName(layer.name);
  const formattedDate = layer.date ? formatDate(layer.date) : '';

  if (layerType === 'spectral') {
    // Use same format as TCI: "Location Index Name - Date"
    return formattedDate ? `${cleanedName} - ${formattedDate}` : cleanedName;
  } else if (layerType === 'tci') {
    return formattedDate ? `${cleanedName} - ${formattedDate}` : cleanedName;
  } else {
    // Geometry layers
    return layer.name;
  }
}

/**
 * Group layers by type for organized display
 */
export function groupLayers(layers: LayerMetadata[]) {
  return {
    tci: layers.filter(l =>
      l.type === 'raster' &&
      l.id !== BASEMAP_LAYER_ID &&
      !isSpectralIndex(l)
    ),
    spectralIndices: layers.filter(l =>
      l.type === 'raster' &&
      l.id !== BASEMAP_LAYER_ID &&
      isSpectralIndex(l)
    ),
    geometries: layers.filter(l => l.type === 'geometry'),
    basemap: layers.filter(l => l.id === BASEMAP_LAYER_ID),
  };
}
