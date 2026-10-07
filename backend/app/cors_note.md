# CORS configuration needed for the web dashboard

The dashboard (web/, default http://localhost:3000) calls the API from the browser, so the backend must allow cross-origin requests, including the custom `x-api-key` header. Add in `backend/app/main.py` after creating `app`:

```python
import os
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("VOCALFACE_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],   # must include x-api-key and content-type
)
```

Also needed:
- Serve `backend/static/` at `/static` (StaticFiles) so `/static/playground.html?cid=...` loads in the dashboard iframe. The dashboard also passes `api_key` and `api` query params to the playground.
- There are no list endpoints for conversations/videos and no update endpoint for personas; the dashboard works around this (local id tracking, "save as new"). Adding `GET /v1/conversations`, `GET /v1/videos`, `PUT /v1/personas/{id}` would remove the workarounds.
