declare module '@maplibre/maplibre-gl-compare' {
  import type { Map } from 'maplibre-gl';

  interface CompareOptions {
    mousemove?: boolean;
    orientation?: 'vertical' | 'horizontal';
  }

  class Compare {
    constructor(a: Map, b: Map, container: string | HTMLElement, options?: CompareOptions);
    remove(): void;
    setSlider(x: number): void;
  }

  export default Compare;
}

declare module '@maplibre/maplibre-gl-compare/maplibre-gl-compare.css';
declare module '@maplibre/maplibre-gl-compare/dist/maplibre-gl-compare.js';
