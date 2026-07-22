"""FastAPI health-check endpoint."""

from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    """Return the current health status of the service."""
    return {"status": "ok"}
