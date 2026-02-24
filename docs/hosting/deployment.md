# Deployment

LLM Benchmark Studio uses a CI/CD pipeline with GitHub Actions for automated builds and deployments.

## CI/CD Pipeline

The pipeline is defined in `.github/workflows/ci.yml` and consists of four jobs:

### 1. Test

Triggers on:

- Push to `main` branch
- Push of version tags (`v*.*.*`)
- Pull requests to `main`

Steps:

1. Checkout code, install `uv` and Python 3.12
2. Install dependencies with `uv sync`
3. **Level 1 tests**: Run all tests except E2E smoke (API contracts, no secrets needed)
4. **Level 2 tests** (main branch only): Run E2E smoke tests requiring `ZAI_API_KEY`

### 2. Build

Runs after tests pass.

Steps:

1. Set up Docker Buildx
2. Log in to GitHub Container Registry (GHCR)
3. Determine app version from git ref (tag or `main-<sha>`)
4. Build Docker image with layer caching (two-stage: Node.js frontend + Python backend)
5. Push to GHCR (except for PRs)
6. Run smoke test: start the container and verify `/healthz` responds within 60 seconds

### 3. Deploy to Staging

Triggers on push to `main` branch (after successful build).

- Image tag: `:main`
- Deploys via Portainer REST API

### 4. Deploy to Production

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
| `ZAI_API_KEY` | API key for Level 2 E2E smoke tests |

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

LLM Benchmark Studio uses WebSocket connections for real-time job status updates. Your reverse proxy must be configured to handle both standard HTTP requests and WebSocket upgrades.

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

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Timeouts: WebSocket connections are long-lived
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

The key WebSocket directives:

- `proxy_http_version 1.1` -- Required for WebSocket upgrade
- `proxy_set_header Upgrade $http_upgrade` -- Passes the Upgrade header from the client
- `proxy_set_header Connection "upgrade"` -- Signals the upstream to switch protocols
- `proxy_read_timeout 3600s` -- Keeps the WebSocket connection alive for long-running jobs (the client sends pings every 60 seconds to maintain the connection)

### Caddy

Caddy handles WebSocket proxying automatically with no extra configuration:

```
benchmark.example.com {
    reverse_proxy localhost:8501
}
```

### Traefik

For Traefik, WebSocket support is enabled by default when using the standard HTTP router. No special middleware is needed.

### Cloudflare

If using Cloudflare as a proxy:

- WebSocket connections are supported on all plans
- Set the **WebSockets** toggle to "On" in the Cloudflare dashboard (Network tab)
- Cloudflare imposes a 100-second idle timeout; the application's 60-second ping interval keeps connections alive within this limit
- For the `/ws` endpoint, ensure the Cloudflare proxy is enabled (orange cloud)

## Health Check

The application exposes a health endpoint used by Docker, CI smoke tests, and monitoring:

```bash
curl http://localhost:8501/healthz
# {"status": "ok", "version": "1.2.0"}
```

### Docker Health Check

The Docker image includes a built-in health check that runs every 30 seconds against `/healthz`:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/healthz')" || exit 1
```

The Docker Compose file includes an identical health check definition. After 3 consecutive failures (90 seconds), Docker marks the container as unhealthy.

### Application Metrics

Use the admin system endpoint for monitoring:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8501/api/admin/system
```

Returns database size, results count, active benchmarks, and uptime.

## WebSocket Endpoint

The WebSocket endpoint is at `/ws` and requires a JWT access token passed as a query parameter:

```
ws://localhost:8501/ws?token=<jwt_access_token>
```

Or over TLS:

```
wss://benchmark.example.com/ws?token=<jwt_access_token>
```

Key behaviors:

- Supports up to 5 concurrent connections per user (multi-tab)
- On connect, sends a `sync` message with active and recent jobs
- Clients should send a `{"type": "ping"}` every 60 seconds to keep the connection alive
- The server closes connections that are idle for 90 seconds (no messages received)
- Clients can send `{"type": "cancel", "job_id": "..."}` to cancel a running job
