import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("api.run:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
