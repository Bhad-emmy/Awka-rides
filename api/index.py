from backend.main import app
from backend.rate_limit import install_rate_limit

install_rate_limit(app)
