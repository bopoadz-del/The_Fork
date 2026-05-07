# Cerebrum Blocks Deployment Configs

Multi-platform deployment configurations for cloud and edge environments.

## 📁 Structure

```
deploy/
├── cloud/              # Docker configs for cloud deployment
│   ├── Dockerfile          # Main API container
│   └── Dockerfile.worker   # Celery worker container
├── render/             # Render.com deployment
│   └── render.yaml         # Render blueprint
├── gcp/                # Google Cloud Platform
│   └── cloudbuild.yaml     # Cloud Build + Cloud Run
├── edge/               # Edge/Jetson deployment
│   └── Dockerfile.jetson   # ARM64/CUDA optimized
└── README.md
```

## 🚀 Quick Deploy

### Render.com (Recommended for Quick Start)

```bash
# Deploy using Render CLI
render blueprint apply deploy/render/render.yaml

# Or push to GitHub - Render auto-deploys from main branch
```

### Docker (Local/Cloud)

```bash
# Build and run locally
docker-compose up -d

# Or build specific targets
docker build -f deploy/cloud/Dockerfile -t cerebrum:cloud .
docker run -p 8000:8000 -e PORT=8000 cerebrum:cloud
```

### Google Cloud Platform

```bash
# Submit build
gcloud builds submit --config deploy/gcp/cloudbuild.yaml

# Deploy to Cloud Run
gcloud run deploy cerebrum-api --image gcr.io/PROJECT/cerebrum-api:latest
```

### NVIDIA Jetson (Edge)

```bash
# Build for ARM64/CUDA
docker build -f deploy/edge/Dockerfile.jetson -t cerebrum:jetson .

# Run on Jetson
docker run -p 8000:8000 --runtime nvidia cerebrum:jetson
```

## ⚙️ Environment Configs

| File | Environment | Profile |
|------|-------------|---------|
| `config/cloud.yaml` | Render/GCP/Cloud | `cloud_render` |
| `config/edge.yaml` | Jetson/Local | `jetson_orin` |

## 🔑 Required Secrets

Create `.env` file:

```bash
# AI Providers
DEEPSEEK_API_KEY=sk-...
GROQ_API_KEY=gsk-...
OPENAI_API_KEY=sk-...

# Database
DATABASE_URL=postgresql://...
REDIS_URL=redis://...

# Auth
JWT_SECRET=your-secret-key
```

## 🏥 Health Checks

- **API Health**: `GET /v1/health`
- **Dashboard**: Static site health via Render
- **Worker**: Celery worker monitor
