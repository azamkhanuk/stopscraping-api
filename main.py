import json
import asyncio
import os
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from pydantic import BaseModel
from typing import List, Dict
from contextlib import asynccontextmanager
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import httpx
import logging

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize FastAPICache
    FastAPICache.init(InMemoryBackend())
    yield

app = FastAPI(lifespan=lifespan)

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "https://stopscraping.me").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Key authentication
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key_header: str = Depends(api_key_header)):
    if api_key_header == os.getenv("API_KEY"):
        return api_key_header   
    raise HTTPException(status_code=403, detail="Could not validate credentials")

@app.get("/api/protected-endpoint")
@limiter.limit("5/minute")
async def protected_route(api_key: str = Depends(get_api_key)):
    return {"message": "This is a protected endpoint"}

# Remember to use HTTPS in production

DATA_FILE = "block_ips.json"
URL_FILE = "ai_urls.json"

class IPData(BaseModel):
    openai: Dict[str, List[str]]

def read_ip_data() -> Dict:
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"openai": {"searchbot": [], "chatgpt-user": [], "gptbot": []}}

def write_ip_data(data: Dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.get("/api/block-ips", response_model=IPData)
@cache(expire=3600)  # Cache for 1 hour
async def get_block_ips():
    return read_ip_data()

@app.get("/api/block-ips/{bot_type}", response_model=Dict[str, List[str]])
@cache(expire=3600)  # Cache for 1 hour
async def get_bot_ips(bot_type: str):
    data = read_ip_data()
    if bot_type in data:
        return {bot_type: data[bot_type]}
    raise HTTPException(status_code=404, detail="Bot type not found")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def read_url_data() -> Dict:
    try:
        with open(URL_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"openai": {}}

@app.get("/api/update-ips")
async def update_ips():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://openai.com/",
    }
    url_data = read_url_data()
    openai_urls = url_data.get("openai", {})
    errors = []

    logger.info("Starting IP update process")
    current_data = read_ip_data()
    updated = False

    async with httpx.AsyncClient(headers=headers) as client:
        await client.get("https://openai.com/")
        
        for bot_type, url in openai_urls.items():
            logger.info(f"Fetching data for {bot_type} from {url}")
            try:
                await asyncio.sleep(1)  # 1 second delay between requests
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()
                logger.info(f"Response content for {bot_type}: {response.text}")
                data = response.json()
                logger.info(f"Parsed JSON for {bot_type}: {data}")
                ip_list = [prefix['ipv4Prefix'] for prefix in data.get('prefixes', [])]
                if ip_list:
                    current_data["openai"][bot_type] = ip_list
                    updated = True
                    logger.info(f"Successfully updated data for {bot_type}: {ip_list}")
                else:
                    logger.warning(f"No IP data found for {bot_type}")
                    errors.append(f"No IP data found for {bot_type}")
            except httpx.HTTPStatusError as exc:
                logger.warning(f"HTTP error occurred for {bot_type}: {exc}")
                errors.append(f"HTTP error occurred for {bot_type}: {exc}")
            except json.JSONDecodeError as exc:
                logger.error(f"JSON decode error for {bot_type}: {exc}")
                errors.append(f"JSON decode error for {bot_type}: {exc}")
            except Exception as exc:
                logger.error(f"Unexpected error occurred for {bot_type}: {exc}")
                errors.append(f"Unexpected error occurred for {bot_type}: {exc}")

    if updated:
        write_ip_data(current_data)
        logger.info("IP data update completed with partial success")
        return {
            "message": "IP data update completed with partial success",
            "data": current_data["openai"],
            "warnings": errors if errors else None
        }
    else:
        error_message = "Failed to retrieve any valid IP data. "
        if errors:
            error_message += f"\n\nErrors encountered: {'; '.join(errors)}"
        logger.error(error_message)
        raise HTTPException(status_code=503, detail=error_message)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))