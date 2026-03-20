FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app

# Install minimal system dependencies
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update && apt-get install -y \
    wget \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install miniconda with Python 3.12 support (ARM64 required for AgentCore)
RUN --mount=type=cache,target=/tmp/conda_cache \
    if [ ! -f /tmp/conda_cache/miniconda.sh ]; then \
        wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh -O /tmp/conda_cache/miniconda.sh; \
    fi && \
    bash /tmp/conda_cache/miniconda.sh -b -p /opt/conda && \
    /opt/conda/bin/conda clean -afy

# Add conda to PATH and accept TOS via environment
ENV PATH="/opt/conda/bin:$PATH"
ENV CONDA_ALWAYS_YES=true
ENV CONDA_AUTO_UPDATE_CONDA=false
ENV CONDA_PLUGINS_AUTO_ACCEPT_TOS=true
ENV CONDA_CHANNEL_PRIORITY=flexible

# Accept conda Terms of Service and install mamba for faster dependency resolution
# Mamba is 10-20x faster than conda (148s → ~10-15s for geospatial packages)
RUN conda install -n base -c conda-forge mamba python=3.12 -y && \
    conda clean -afy

# Copy requirements first for better Docker layer caching
COPY requirements.txt requirements.txt

# Install geospatial packages via conda-forge (handles all C dependencies)
# Updated package versions compatible with Python 3.12
# Ultra-optimized: Use mamba for 10-20x faster dependency resolution (~130s saved!)
RUN --mount=type=cache,target=/opt/conda/pkgs \
    --mount=type=cache,target=/root/.conda/pkgs \
    mamba install -c conda-forge -y \
    gdal=3.12.1 \
    fiona=1.10.1 \
    shapely=2.1.2 \
    geopandas=1.1.2 \
    pyproj=3.7.2 \
    numpy=2.4.0 \
    pandas=2.3.3 \
    rasterio=1.5.0 \
    rasterstats=0.20.0 \
    geopy=2.4.1 \
    osmnx=2.0.7 \
    proj=9.7.1 \
    geos=3.14.1 \
    && conda clean -afy

# Install remaining Python packages via pip
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install aws-opentelemetry-distro>=0.10.0

# Set AWS environment variables
ENV AWS_REGION=us-east-1
ENV AWS_DEFAULT_REGION=us-east-1
ENV DOCKER_CONTAINER=1

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080
EXPOSE 8000

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Copy project files
COPY . .

CMD ["opentelemetry-instrument", "python", "-m", "geospatial_agent_on_aws"]