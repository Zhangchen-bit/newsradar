"""Launch the FastAPI server on localhost:8765."""
from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run("api:app", host="127.0.0.1", port=8765, log_level="info",
                reload=False)
