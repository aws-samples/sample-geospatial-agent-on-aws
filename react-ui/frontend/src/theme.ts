// Material Design 3 Theme Configuration
export const theme = {
  colors: {
    primary: '#000000',
    onPrimary: '#FFFFFF',
    primaryContainer: '#E0E0E0',
    secondary: '#424242',
    onSecondary: '#FFFFFF',
    surface: '#FFFFFF',
    onSurface: '#1C1B1F',
    surfaceVariant: '#F5F5F5',
    background: '#FAFAFA',
    onBackground: '#1C1B1F',
    error: '#B00020',
    onError: '#FFFFFF',
    success: '#2E7D32',
    outline: '#E0E0E0',
    outlineVariant: '#C4C4C4',
  },
  
  elevation: {
    level0: 'none',
    level1: '0 1px 3px rgba(0,0,0,0.12)',
    level2: '0 2px 4px rgba(0,0,0,0.14)',
    level3: '0 4px 8px rgba(0,0,0,0.16)',
    level4: '0 8px 16px rgba(0,0,0,0.18)',
  },
  
  spacing: {
    xs: '4px',
    sm: '8px',
    md: '16px',
    lg: '24px',
    xl: '32px',
    xxl: '40px',
  },
  
  borderRadius: {
    sm: '4px',
    md: '8px',
    lg: '12px',
    full: '50%',
  },
  
  typography: {
    headlineLarge: {
      fontSize: '32px',
      fontWeight: 400,
      lineHeight: 1.2,
    },
    headlineMedium: {
      fontSize: '28px',
      fontWeight: 400,
      lineHeight: 1.3,
    },
    titleLarge: {
      fontSize: '22px',
      fontWeight: 500,
      lineHeight: 1.4,
    },
    titleMedium: {
      fontSize: '16px',
      fontWeight: 500,
      lineHeight: 1.5,
    },
    bodyLarge: {
      fontSize: '16px',
      fontWeight: 400,
      lineHeight: 1.5,
    },
    bodyMedium: {
      fontSize: '14px',
      fontWeight: 400,
      lineHeight: 1.5,
    },
    labelLarge: {
      fontSize: '14px',
      fontWeight: 500,
      lineHeight: 1.4,
    },
  },
  
  transitions: {
    short: '200ms cubic-bezier(0.4, 0.0, 0.2, 1)',
    medium: '300ms cubic-bezier(0.4, 0.0, 0.2, 1)',
  },
  
  states: {
    hover: 'rgba(0, 0, 0, 0.04)',
    active: 'rgba(0, 0, 0, 0.12)',
    disabled: 0.75,  // Increased from 0.38 for better visibility
  },
};
