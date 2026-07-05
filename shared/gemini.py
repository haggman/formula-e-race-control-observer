"""One place to build the Gemini client, so every agent hits the right endpoint.

Newer Vertex models (Gemini 3.x, e.g. gemini-3.5-flash) are served on the GLOBAL
endpoint, not regional ones — a us-central1 client 404s on them. So on Vertex we
build the client with location=global (covers old and new models); override with
FE_VERTEX_LOCATION if you need a specific region. Off Vertex, it's the Gemini
Developer API (GOOGLE_API_KEY).
"""
from __future__ import annotations

import os


def make_client():
    """Return a genai.Client aimed at the right backend + endpoint."""
    from google import genai

    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true"):
        return genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("FE_VERTEX_LOCATION", "global"),
        )
    return genai.Client()          # Gemini Developer API via GOOGLE_API_KEY
