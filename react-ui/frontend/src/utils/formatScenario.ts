/**
 * Utility functions for formatting scenario data for display
 */

export interface ScenarioDates {
  before?: string;
  after?: string;
  fire_start?: string;
}

export interface ScenarioToolCall {
  name: string;
  params: Record<string, any>;
  result: string;
}

export interface ScenarioConfig {
  name: string;
  location: string;
  dates: ScenarioDates;
  narrative: string;
  tool_calls?: ScenarioToolCall[];
}

/**
 * Formats scenario config into user-friendly markdown for display
 */
export function formatScenarioAnalysis(config: ScenarioConfig): string {
  const { narrative } = config;

  // Just return the narrative - it already contains the formatted agent response
  // The narrative markdown files are pre-formatted with the analysis results
  return narrative || 'No analysis available.';
}
