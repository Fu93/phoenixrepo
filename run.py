import os

import uvicorn

from api.run import app
from config.settings import Settings

if __name__ == "__main__":
    settings = Settings()
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", str(settings.port))))
