import httpx

shared_async_client = httpx.AsyncClient(
    timeout=15,
    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
)
