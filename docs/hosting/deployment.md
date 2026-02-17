# Deployment

LLM Benchmark Studio uses a CI/CD pipeline with GitHub Actions for automated builds and deployments.

## CI/CD Pipeline

The pipeline is defined in `.github/workflows/ci.yml` and consists of three jobs:

### 1. Build

Triggers on:

- Push to `main` branch
- Push of version tags (`v*.*.*`)
- Pull requests to `main`

Steps:

1. Checkout code
2. Set up Docker Buildx
3. Log in to GitHub Container Registry (GHCR)
4. Build Docker image with caching
5. Push to GHCR (except for PRs)
6. Run smoke test (start container, verify `/healthz`)

### 2. Deploy to Staging

Triggers on push to `main` branch (after successful build).

- Image tag: `:main`
- Deploys via Portainer REST API

### 3. Deploy to Production

Triggers on version tags (`v*.*.*`) (after successful build).

- Image tag: `:<major>.<minor>`
- Deploys via Portainer REST API

## Container Registry

Images are published to GitHub Container Registry:

```
ghcr.io/maheidem/llm-benchmark-studio
```

Tags:

| Tag Pattern | Example | Trigger |
|-------------|---------|---------|
| `:main` | `:main` | Push to main branch |
| `:sha-<hash>` | `:sha-abc1234` | Every push |
| `:<version>` | `:1.2.0` | Version tag `v1.2.0` |
| `:<major>.<minor>` | `:1.2` | Version tag `v1.2.x` |
| `:latest` | `:latest` | Push to main branch |

## Deployment Method

The application deploys to a Portainer-managed Docker environment:

1. **Pull**: Latest image from GHCR
2. **Stop**: Existing stack
3. **Remove**: Old containers
4. **Start**: Stack (recreates containers with new image)

### Required Secrets

Configure these in your GitHub repository settings:

| Secret | Description |
|--------|-------------|
| `PORTAINER_URL` | Portainer API base URL |
| `PORTAINER_API_KEY` | Portainer API authentication key |
| `PORTAINER_ENDPOINT_ID` | Docker endpoint ID in Portainer |
| `PORTAINER_STACK_ID_STAGING` | Staging stack ID |
| `PORTAINER_STACK_ID_PROD` | Production stack ID |

## Staging vs Production

| Setting | Staging | Production |
|---------|---------|-----------|
| Port | 8502 | 8501 |
| Image tag | `:main` | `:<major>.<minor>` |
| Auto-deploy | On push to main | On version tag |

## Manual Deployment

To deploy manually without the CI/CD pipeline:

```bash
# Build the image
docker build --build-arg APP_VERSION=1.2.0 -t llm-benchmark-studio:1.2.0 .

# Run the container
docker run -d \
  -p 8501:8501 \
  -v ./data:/app/data \
  -v ./.env:/app/.env:ro \
  -e JWT_SECRET=your-secret \
  -e COOKIE_SECURE=true \
  --name benchmark-studio \
  --restart unless-stopped \
  llm-benchmark-studio:1.2.0
```

## Reverse Proxy

For production deployments behind a reverse proxy:

### Nginx

```nginx
server {
    listen 443 ssl;
    server_name benchmark.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8501;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE support
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

### Caddy

```
benchmark.example.com {
    reverse_proxy localhost:8501
}
```

!!! important "SSE Proxying"
    The benchmark and tool eval endpoints use Server-Sent Events (SSE). Make sure your reverse proxy disables response buffering (`proxy_buffering off` in nginx, or `X-Accel-Buffering: no` which the app already sets).

## Monitoring

### Health Check

The application exposes a health endpoint:

```bash
curl http://localhost:8501/healthz
# {"status": "ok", "version": "1.2.0"}
```

### Docker Health Check

The Docker image includes a built-in health check that runs every 30 seconds against `/healthz`.

### Application Metrics

Use the admin system endpoint for monitoring:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8501/api/admin/system
```

Returns database size, results count, active benchmarks, and uptime.
