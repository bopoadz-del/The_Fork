# Docker Hub Setup Guide

## For Repository Owner (bopoadz-del)

### 1. Create Docker Hub Account & Repository

1. Go to https://hub.docker.com/
2. Sign up / Log in
3. Click "Create Repository"
4. Name: `cerebrum-blocks`
5. Visibility: Public
6. Click "Create"

### 2. Create Access Token

1. Go to https://hub.docker.com/settings/security
2. Click "New Access Token"
3. Name: `github-actions`
4. Access Permissions: `Read, Write, Delete`
5. Click "Generate"
6. **COPY THE TOKEN** (you won't see it again!)

### 3. Add Secrets to GitHub Repository

1. Go to https://github.com/bopoadz-del/cerebrum-blocks/settings/secrets/actions
2. Click "New repository secret"
3. Add two secrets:
   - Name: `DOCKER_HUB_USERNAME` → Value: your Docker Hub username
   - Name: `DOCKER_HUB_TOKEN` → Value: the token you just copied

### 4. Trigger a Build

Push a new tag to trigger the Docker build:

```bash
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions will automatically build and push multi-platform images to Docker Hub.

---

## For Users

### Pull and Run

```bash
# Pull the image
docker pull bopoadz-del/cerebrum-blocks:latest

# Run with default settings
docker run -p 8000:8000 bopoadz-del/cerebrum-blocks:latest

# Run with your API keys
docker run -p 8000:8000 \
  -e DEEPSEEK_API_KEY=your_deepseek_key \
  -e GROQ_API_KEY=your_groq_key \
  -e OPENAI_API_KEY=your_openai_key \
  -e ANTHROPIC_API_KEY=your_anthropic_key \
  bopoadz-del/cerebrum-blocks:latest

# Run with persistent data
docker run -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -e DATA_DIR=/app/data \
  bopoadz-del/cerebrum-blocks:latest
```

### Using Docker Compose

```bash
# Clone the repo
git clone https://github.com/bopoadz-del/cerebrum-blocks.git
cd cerebrum-blocks

# Copy and edit environment variables
cp .env.example .env
# Edit .env with your API keys

# Start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

---

## Multi-Platform Support

The Docker image supports multiple architectures:
- `linux/amd64` (Intel/AMD 64-bit)
- `linux/arm64` (ARM 64-bit, Apple Silicon, AWS Graviton)
- `linux/arm/v7` (ARM 32-bit, Raspberry Pi)

Docker automatically pulls the correct architecture for your system.

---

## Available Tags

| Tag | Description |
|-----|-------------|
| `latest` | Latest stable release |
| `v1.0.0` | Specific version |
| `v1.0` | Latest patch of v1.0.x |
| `v1` | Latest minor of v1.x.x |
| `main` | Latest commit on main branch |
| `sha-abc123` | Specific commit |

---

## Manual Build (if needed)

```bash
# Build locally
docker build -t cerebrum-blocks:local .

# Build for specific platform
docker build --platform linux/arm64 -t cerebrum-blocks:arm64 .

# Build multi-platform (requires buildx)
docker buildx create --use
docker buildx build --platform linux/amd64,linux/arm64,linux/arm/v7 -t cerebrum-blocks:multi .
```
