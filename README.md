# Fast Docker Proxy

Fast Docker Proxy is a **FastAPI-based Docker Registry reverse proxy** inspired by [cloudflare-docker-proxy](https://github.com/ciiiii/cloudflare-docker-proxy).

It sits in front of one or more upstream registries (Docker Hub, GHCR, GCR, etc.), handling:

- Docker Registry v2 API proxying
- Bearer token authentication flow (`/v2/` + `/v2/auth`)
- Docker Hub `library/*` path auto-completion (e.g. `busybox` → `library/busybox`)
- Docker Hub blob download redirects (307) by following them server-side
- Multiple upstream registries routed by hostname (e.g. `docker.example.com`, `ghcr.example.com`, …)

Use it as a drop-in HTTPS endpoint for Docker clients (as a registry mirror or as a custom registry host).

## Features

- 🌐 **Multiple upstream registries** via hostname routing:
  - `docker.<CUSTOM_DOMAIN>` → `https://registry-1.docker.io`
  - `quay.<CUSTOM_DOMAIN>` → `https://quay.io`
  - `gcr.<CUSTOM_DOMAIN>` → `https://gcr.io`
  - `k8s-gcr.<CUSTOM_DOMAIN>` → `https://k8s.gcr.io`
  - `k8s.<CUSTOM_DOMAIN>` → `https://registry.k8s.io`
  - `ghcr.<CUSTOM_DOMAIN>` → `https://ghcr.io`
  - `cloudsmith.<CUSTOM_DOMAIN>` → `https://docker.cloudsmith.io`
  - `ecr.<CUSTOM_DOMAIN>` → `https://public.ecr.aws`
  - `docker-staging.<CUSTOM_DOMAIN>` → `https://registry-1.docker.io`
- 🔐 **Full auth flow support**
  - Proxies `/v2/` to probe auth
  - Proxies `/v2/auth` to fetch Bearer tokens
  - Adds Docker Hub–style `WWW-Authenticate` headers for clients
- 🧠 **Docker Hub compatibility**
  - Auto-inserts `library/` for official images
  - Manually follows 307 redirects for blob downloads
- 🐍 **Pure Python / FastAPI / httpx**
  - Easy to run as a standalone service or behind nginx / Caddy / Traefik
  - Suitable for self-hosted setups / labs / testing environments
- ⚡ **High performance**
  - Async I/O with httpx for concurrent requests
  - Streaming responses for large blobs
  - Minimal RAM / disk usage

## Configuration

The proxy behavior is controlled via environment variables:
- CUSTOM_DOMAIN
  The base domain used for routing (default: example.com).
  Example:
  - docker.example.com → Docker Hub
  - ghcr.example.com → GitHub Container Registry
- MODE
  Mode of operation:
  - `prod` (default): normal behavior
  - `debug`: if the host is not in the routing table, fall back to TARGET_UPSTREAM.
- TARGET_UPSTREAM
  Only used when MODE=debug.
  Default: `https://registry-1.docker.io`.

## Running the Proxy

### Directly with Python

You can simply run:

```sh
export CUSTOM_DOMAIN=example.com
export MODE=prod

python main.py
```

This will start the HTTP server on:
- http://127.0.0.1:5000

You will typically place a TLS-terminating reverse proxy (nginx / Caddy / Traefik) in front of it.

### Using uvicorn directly

Alternatively, you can run it via uvicorn:

```sh
export CUSTOM_DOMAIN=example.com
export MODE=prod

uvicorn main:app --host 0.0.0.0 --port 5000 --log-level debug
```

Adjust the module path (main:app) to match your file / package layout.

## Using with Docker

### Pull from the proxy by hostname

Once DNS + TLS + nginx are set up, you can pull images via your proxy:

```sh
docker pull docker.example.com/library/busybox:latest
```

For Docker Hub official images (like busybox, alpine, etc.), the proxy will:
- Accept requests like `/v2/busybox/manifests/latest`
- Internally rewrite to `/v2/library/busybox/manifests/latest` for Docker Hub
- Handle token auth + blob redirects for you

You can also pull non-library images, e.g.:

```sh
docker pull docker.example.com/bitnami/mariadb:latest
```

### Use as a registry mirror (daemon-level)

On some setups you can configure Docker's daemon to use your proxy as a registry mirror (though this proxy behaves slightly differently from a pure mirror, since it's a smart HTTP proxy with auth logic).

Example `/etc/docker/daemon.json` snippet:

```json
{
  "registry-mirrors": [
    "https://docker.example.com"
  ]
}
```

Restart Docker afterwards.

Note: exact behavior may depend on your environment and how strict your registry usage is. For complex setups (multiple upstreams, private images, etc.), using explicit hostnames (`docker.example.com/...`) is often easier to reason about.

## Limitations & Notes

This is a reverse proxy, not a full registry implementation. It relies on the upstream registry to:
- Provide authentication (WWW-Authenticate),
- Serve `/v2/` and `/v2/auth`,
- Host the actual image data.

If an image / tag is removed or no longer available on Docker Hub (e.g. some Bitnami images), the proxy will also return the same errors (e.g. 404 / manifest unknown).

For production scenarios, you may want to add:
- Logging / metrics (e.g. via middleware),
- Rate limiting / access control,
- Persistent TLS termination with a proper reverse proxy.

Use with caution in production and review the upstream registries' terms of service.
