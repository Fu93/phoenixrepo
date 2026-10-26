import os

import uvicorn

if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    uvicorn.run("api.run:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
