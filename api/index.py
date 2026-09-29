import traceback
from fastapi import FastAPI

try:
    from backend.main import app
    from backend.rate_limit import install_rate_limit
    install_rate_limit(app)
except Exception as exc:
    app = FastAPI()

    @app.get("/{path:path}")
    async def import_error(path: str):
        return {
            "imported": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
