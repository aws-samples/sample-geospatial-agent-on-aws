# Multi-stage build for React UI (Frontend + Backend combined)
# This Dockerfile builds both the frontend and backend, serving frontend via backend

# Stage 1: Build frontend
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy frontend package files
COPY frontend/package*.json ./

# Install dependencies
RUN npm ci

# Copy frontend source
COPY frontend/ ./

# Build frontend
RUN npm run build

# Stage 2: Build backend and serve
FROM node:20-alpine AS production

WORKDIR /app

# Copy backend package files
COPY backend/package*.json ./

# Install all dependencies (need devDependencies for TypeScript build)
RUN npm ci

# Copy backend source
COPY backend/ ./

# Build TypeScript backend
RUN npm run build

# Remove devDependencies after build to reduce image size
RUN npm prune --production

# Copy built frontend from previous stage
COPY --from=frontend-builder /app/frontend/dist ./public

# Create a simple static file server in the backend
# The backend will serve the frontend at root and API at /api/*

# Create non-root user for security
RUN addgroup -g 1001 -S nodejs && adduser -S nodejs -u 1001

# Set environment variables
ENV NODE_ENV=production
ENV PORT=3001

# Change ownership and switch to non-root user
RUN chown -R nodejs:nodejs /app
USER nodejs

# Expose port
EXPOSE 3001

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3001/health', (r) => {process.exit(r.statusCode === 200 ? 0 : 1)})"

# Start the application
CMD ["node", "dist/index.js"]
