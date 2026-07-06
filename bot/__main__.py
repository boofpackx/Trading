import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "bot.server:app",
        host="127.0.0.1",
        port=int(os.getenv("PORT", "8000")),
        log_level="warning",
    )
