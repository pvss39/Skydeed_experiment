"""
start.py — Production entrypoint.
Reads PORT from environment (set by Railway) and starts uvicorn.
"""
import os
import uvicorn

port = int(os.environ.get("PORT", 8080))
uvicorn.run("api.main:app", host="0.0.0.0", port=port)
