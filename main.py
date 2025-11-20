import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from urllib.parse import urljoin, urlencode
import os
import uvicorn
import dotenv

dotenv.load_dotenv()

app = FastAPI()

CUSTOM_DOMAIN = os.getenv("CUSTOM_DOMAIN", "example.com")
MODE = os.getenv("MODE", "prod")
TARGET_UPSTREAM = os.getenv("TARGET_UPSTREAM", "https://registry-1.docker.io")

dockerHub = "https://registry-1.docker.io"

routes = {
    f"docker.{CUSTOM_DOMAIN}": dockerHub,
    f"quay.{CUSTOM_DOMAIN}": "https://quay.io",
    f"gcr.{CUSTOM_DOMAIN}": "https://gcr.io",
    f"k8s-gcr.{CUSTOM_DOMAIN}": "https://k8s.gcr.io",
    f"k8s.{CUSTOM_DOMAIN}": "https://registry.k8s.io",
    f"ghcr.{CUSTOM_DOMAIN}": "https://ghcr.io",
    f"cloudsmith.{CUSTOM_DOMAIN}": "https://docker.cloudsmith.io",
    f"ecr.{CUSTOM_DOMAIN}": "https://public.ecr.aws",
    f"docker-staging.{CUSTOM_DOMAIN}": dockerHub,
}


def route_by_host(host: str) -> str:
    if host in routes:
        return routes[host]
    if MODE == "debug":
        return TARGET_UPSTREAM
    return ""


# -----------------------------
# Helpers
# -----------------------------


async def fetch_with_client(url, method="GET", headers=None, follow_redirects=True):
    async with httpx.AsyncClient(
        follow_redirects=follow_redirects, timeout=30
    ) as client:
        return await client.request(method, url, headers=headers)


def parse_www_authenticate(auth_header: str):
    # example: Bearer realm="https://auth.docker.com/token",service="registry.docker.io"
    parts = auth_header.split('"')
    realm = parts[1]
    service = parts[3]
    return realm, service


async def fetch_token(realm, service, scope, authorization):
    params = {}
    if service:
        params["service"] = service
    if scope:
        params["scope"] = scope

    url = realm + "?" + urlencode(params)
    headers = {}
    if authorization:
        headers["Authorization"] = authorization

    return await fetch_with_client(url, headers=headers)


def response_unauthorized(url: str):
    scheme = "http" if MODE == "debug" else "https"
    www_auth = (
        f'Bearer realm="{scheme}://{url}/v2/auth",service="cloudflare-docker-proxy"'
    )

    return JSONResponse(
        status_code=401,
        content={"message": "UNAUTHORIZED"},
        headers={"WWW-Authenticate": www_auth},
    )


# -----------------------------
# Main Proxy Logic
# -----------------------------


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(full_path: str, request: Request):

    # 1. Determine upstream
    host = request.headers.get("host")
    upstream = route_by_host(host)

    if upstream == "":
        return JSONResponse(status_code=404, content={"routes": list(routes.keys())})

    is_dockerhub = upstream == dockerHub

    # reconstruct URL
    original_url = request.url
    path = "/" + full_path

    # 2. Redirect "/" → "/v2/"
    if path == "/":
        return RedirectResponse(
            url=f"{original_url.scheme}://{host}/v2/", status_code=301
        )

    # 3. === /v2/ request ===
    if path == "/v2/":
        upstream_url = urljoin(upstream, "/v2/")
        headers = {}
        auth = request.headers.get("Authorization")
        if auth:
            headers["Authorization"] = auth

        resp = await fetch_with_client(upstream_url, headers=headers)

        if resp.status_code == 401:
            return response_unauthorized(host)

        return Response(
            content=resp.content, status_code=resp.status_code, headers=resp.headers
        )

    # 4. === /v2/auth ===
    if path == "/v2/auth":
        probe_url = urljoin(upstream, "/v2/")
        probe_resp = await fetch_with_client(probe_url)

        if probe_resp.status_code != 401:
            return Response(probe_resp.content, status_code=probe_resp.status_code)

        auth_header = probe_resp.headers.get("WWW-Authenticate")
        if auth_header is None:
            return Response(probe_resp.content, status_code=probe_resp.status_code)

        realm, service = parse_www_authenticate(auth_header)

        # autocomplete DockerHub scope
        scope = dict(request.query_params).get("scope")
        if scope and is_dockerhub:
            parts = scope.split(":")
            if len(parts) == 3 and "/" not in parts[1]:
                parts[1] = "library/" + parts[1]
                scope = ":".join(parts)

        authorization = request.headers.get("Authorization")

        token_resp = await fetch_token(realm, service, scope, authorization)
        return Response(
            content=token_resp.content,
            status_code=token_resp.status_code,
            headers=token_resp.headers,
        )

    # 5. DockerHub library auto-prefix
    if is_dockerhub:
        parts = path.split("/")
        # path example: /v2/busybox/manifests/latest → 5 segments
        if len(parts) == 5:
            parts.insert(2, "library")
            new_path = "/".join(parts)
            new_url = f"{original_url.scheme}://{host}{new_path}"
            return RedirectResponse(url=new_url, status_code=301)

    # 6. Proxy other requests
    upstream_url = upstream + path

    hop_by_hop = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    }

    forward_headers = {}
    for k, v in request.headers.items():
        if k.lower() in hop_by_hop:
            continue
        forward_headers[k] = v

    # For DockerHub, blob requests return 307 and client must follow manually
    follow = False if is_dockerhub else True

    resp = await fetch_with_client(
        upstream_url,
        method=request.method,
        headers=forward_headers,
        follow_redirects=follow,
    )

    print(
        f"Proxying: {upstream_url}, headers: {forward_headers}, status: {resp.status_code}"
    )

    # 6A. Unauthorized
    if resp.status_code == 401:
        return response_unauthorized(host)

    # 6B. DockerHub blob redirect (must manually follow)
    if is_dockerhub and resp.status_code == 307:
        location = resp.headers.get("Location")
        blob_resp = await fetch_with_client(location)
        return Response(
            blob_resp.content,
            status_code=blob_resp.status_code,
            headers=blob_resp.headers,
        )

    return Response(resp.content, status_code=resp.status_code, headers=resp.headers)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000, log_level="debug")
