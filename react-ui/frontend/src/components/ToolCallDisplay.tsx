import { useState } from 'react';
import type { ToolCall } from '../types.ts';
import { theme } from '../theme';

interface ToolCallDisplayProps {
  tools: ToolCall[];
}

interface ToolCallItemProps {
  tool: ToolCall;
}

function ToolCallItem({ tool }: ToolCallItemProps) {
  const [expanded, setExpanded] = useState(false);

  const statusColor = tool.status === 'executing' ? theme.colors.secondary : theme.colors.success;
  const statusText = tool.status === 'executing' ? 'Running' : 'Completed';

  return (
    <div 
      style={{ 
        marginBottom: theme.spacing.xs,
        borderLeft: `3px solid ${statusColor}`,
        paddingLeft: theme.spacing.sm,
      }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          width: '100%',
          textAlign: 'left',
          padding: `${theme.spacing.xs} ${theme.spacing.sm}`,
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          ...theme.typography.bodyMedium,
          color: theme.colors.onSurface,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          transition: theme.transitions.short,
          borderRadius: theme.borderRadius.sm,
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.backgroundColor = theme.states.hover;
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.backgroundColor = 'transparent';
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: theme.spacing.sm }}>
          <span style={{ ...theme.typography.labelLarge, fontWeight: 500 }}>{tool.name}</span>
          <span 
            style={{ 
              fontSize: '12px',
              fontWeight: 500,
              color: statusColor,
              backgroundColor: `${statusColor}20`,
              padding: `2px ${theme.spacing.xs}`,
              borderRadius: theme.borderRadius.sm,
            }}
          >
            {statusText}
          </span>
        </div>
        <span 
          style={{ 
            fontSize: '10px', 
            color: theme.colors.secondary,
            transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: theme.transitions.short,
            display: 'inline-block',
          }}
        >
          ▼
        </span>
      </button>

      {expanded && (
        <div
          style={{
            marginTop: theme.spacing.xs,
            marginLeft: theme.spacing.sm,
            padding: theme.spacing.md,
            backgroundColor: theme.colors.surfaceVariant,
            borderRadius: theme.borderRadius.sm,
            ...theme.typography.bodyMedium,
          }}
        >
          {tool.params && Object.keys(tool.params).length > 0 && (
            <div style={{ marginBottom: theme.spacing.md }}>
              <div style={{ 
                ...theme.typography.labelLarge, 
                color: theme.colors.secondary,
                marginBottom: theme.spacing.xs 
              }}>
                Input Parameters
              </div>
              <div style={{ 
                padding: theme.spacing.sm,
                backgroundColor: theme.colors.surface,
                borderRadius: theme.borderRadius.sm,
                border: `1px solid ${theme.colors.outlineVariant}`,
              }}>
                {Object.entries(tool.params).map(([key, value]) => (
                  <div key={key} style={{ marginBottom: theme.spacing.xs }}>
                    <span style={{ color: theme.colors.secondary, fontWeight: 500 }}>{key}:</span>{' '}
                    <span style={{ color: theme.colors.onSurface }}>{String(value)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {tool.result && (
            <div>
              <div style={{ 
                ...theme.typography.labelLarge, 
                color: theme.colors.secondary,
                marginBottom: theme.spacing.xs 
              }}>
                Output
              </div>
              <pre
                style={{
                  padding: theme.spacing.sm,
                  backgroundColor: theme.colors.surface,
                  borderRadius: theme.borderRadius.sm,
                  fontSize: '11px',
                  overflow: 'auto',
                  maxHeight: '300px',
                  fontFamily: 'monospace',
                  whiteSpace: 'pre-wrap',
                  wordWrap: 'break-word',
                  border: `1px solid ${theme.colors.outlineVariant}`,
                  margin: 0,
                  color: theme.colors.onSurface,
                }}
              >
                {typeof tool.result === 'string'
                  ? tool.result
                  : JSON.stringify(tool.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ToolCallDisplay({ tools }: ToolCallDisplayProps) {
  const [expanded, setExpanded] = useState(false);

  if (tools.length === 0) {
    return null;
  }

  const uniqueTools: Record<string, ToolCall> = {};
  for (const tool of tools) {
    const toolId = tool.id || `temp_${tool.name}`;
    uniqueTools[toolId] = tool;
  }

  const uniqueToolList = Object.values(uniqueTools);

  return (
    <div
      style={{
        backgroundColor: theme.colors.surface,
        borderRadius: theme.borderRadius.md,
        border: `1px solid ${theme.colors.outlineVariant}`,
        overflow: 'hidden',
        boxShadow: theme.elevation.level1,
      }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          width: '100%',
          textAlign: 'left',
          padding: `${theme.spacing.sm} ${theme.spacing.md}`,
          background: theme.colors.surfaceVariant,
          border: 'none',
          cursor: 'pointer',
          ...theme.typography.labelLarge,
          color: theme.colors.secondary,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          transition: theme.transitions.short,
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.backgroundColor = theme.colors.surfaceVariant;
          e.currentTarget.style.filter = 'brightness(0.95)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.backgroundColor = theme.colors.surfaceVariant;
          e.currentTarget.style.filter = 'none';
        }}
      >
        <span>
          {uniqueToolList.length} tool{uniqueToolList.length !== 1 ? 's' : ''} executed
        </span>
        <span 
          style={{ 
            fontSize: '12px', 
            color: theme.colors.secondary,
            transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: theme.transitions.short,
            display: 'inline-block',
          }}
        >
          ▼
        </span>
      </button>

      {expanded && (
        <div style={{ padding: theme.spacing.md, paddingTop: theme.spacing.sm }}>
          {uniqueToolList.map((tool, index) => (
            <ToolCallItem key={tool.id || index} tool={tool} />
          ))}
        </div>
      )}
    </div>
  );
}
