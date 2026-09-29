import traceback
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def debug_import():
    try:
        from backend.main import app as main_app
        return {"imported": True, "app": str(main_app)}
    except Exception as exc:
        return {
            "imported": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
