import json
import asyncio
import os
from fastapi import FastAPI, HTTPException, Depends, Request, Security, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from typing import List, Dict
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import httpx
import logging
from fastapi.responses import JSONResponse
from functools import lru_cache, wraps
from supabase import create_client, Client
from datetime import datetime, timedelta, time
from dotenv import load_dotenv
from fastapi.encoders import jsonable_encoder
from postgrest.exceptions import APIError
import postgrest

# Load environment variables from .env file
load_dotenv()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# Supabase client setup
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in the .env file")

supabase: Client = create_client(supabase_url, supabase_key)

# Rate limiting
def get_user_id_for_limit(request: Request):
    return request.state.user_id

limiter = Limiter(key_func=get_user_id_for_limit)
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

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def verify_api_key(api_key: str = Security(api_key_header)):
    logger.info(f"Verifying API key: {api_key}")
    if not api_key:
        logger.warning("No API key provided")
        raise HTTPException(status_code=403, detail="API Key is required")
    
    # Query Supabase for the API key
    logger.info(f"Querying Supabase for API key: {api_key}")
    result = supabase.table("api_keys").select("*").eq("api_key", api_key).eq("is_active", True).execute()
    
    logger.info(f"Supabase query result: {result}")
    
    if not result.data:
        logger.warning(f"Invalid or inactive API key: {api_key}")
        raise HTTPException(status_code=403, detail="Invalid or inactive API Key")
    
    api_key_data = result.data[0]
    logger.info(f"API key data: {api_key_data}")
    return {
        "api_key": api_key,
        "tier": api_key_data['tier'],
        "user_id": api_key_data['user_id']
    }

def format_time_until_reset(seconds):
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours} hours, {minutes} minutes"
    elif minutes > 0:
        return f"{minutes} minutes, {seconds} seconds"
    else:
        return f"{seconds} seconds"

def tier_limit():
    def decorator(func):
        @wraps(func)
        async def wrapper(api_key_data: dict = Depends(verify_api_key), *args, **kwargs):
            logger.info(f"In tier_limit wrapper. API key data: {api_key_data}")
            user_id = api_key_data['user_id']
            tier = api_key_data['tier']
            
            logger.info(f"Checking API usage for user_id: {user_id}, tier: {tier}")
            allowed, reset_time = await check_and_update_api_usage(user_id, tier)
            if allowed:
                logger.info(f"API usage check passed for user_id: {user_id}")
                return await func(api_key_data=api_key_data, *args, **kwargs)
            else:
                logger.warning(f"API call limit exceeded for user_id: {user_id}")
                time_until_reset = reset_time - datetime.utcnow()
                formatted_time = format_time_until_reset(time_until_reset.total_seconds())
                raise HTTPException(
                    status_code=429, 
                    detail=f"API call limit exceeded. Resets in {formatted_time}."
                )
        
        return wrapper
    return decorator

# Update all protected endpoints to use the verify_api_key dependency
@app.get("/protected-endpoint")
@tier_limit()
async def protected_route(api_key_data: dict = Depends(verify_api_key)):
    return {"message": "This is a protected endpoint", "tier": api_key_data['tier']}

DATA_FILE = "block_ips.json"
URL_FILE = "ai_urls.json"

class IPData(BaseModel):
    openai: Dict[str, List[str]]

    class Config:
        schema_extra = {
            "example": {
                "openai": {
                    "searchbot": ["192.168.1.1/24"],
                    "chatgpt-user": ["10.0.0.1/24"],
                    "gptbot": ["172.16.0.1/24"]
                }
            }
        }

def read_ip_data() -> Dict:
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            logger.info(f"Read IP data: {data}")
            return data
    except FileNotFoundError:
        logger.warning(f"File {DATA_FILE} not found. Returning default data.")
        return {"openai": {"searchbot": [], "chatgpt-user": [], "gptbot": []}}
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {DATA_FILE}. Returning default data.")
        return {"openai": {"searchbot": [], "chatgpt-user": [], "gptbot": []}}

def write_ip_data(data: Dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.get("/block-ips")
@tier_limit()
async def get_block_ips(api_key_data: dict = Depends(verify_api_key)):
    logger.info(f"get_block_ips called with api_key_data: {api_key_data}")
    try:
        data = read_ip_data()
        logger.info(f"IP data read: {data}")
        return jsonable_encoder(IPData(openai=data["openai"]))
    except Exception as e:
        logger.error(f"Error in get_block_ips: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/block-ips/{bot_type}", response_model=Dict[str, List[str]])
@tier_limit()
@lru_cache(maxsize=32)
async def get_bot_ips(bot_type: str, api_key_data: dict = Depends(verify_api_key)):
    data = read_ip_data()
    if bot_type in data["openai"]:
        return {bot_type: data["openai"][bot_type]}
    raise HTTPException(status_code=404, detail="Bot type not found")

def read_url_data() -> Dict:
    try:
        with open(URL_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"openai": {}}

UPDATE_IP_PASS = os.getenv("UPDATE_IP_PASS")
if not UPDATE_IP_PASS:
    raise ValueError("UPDATE_IP_PASS must be set in the .env file")

@app.get("/update-ips")
async def update_ips(x_update_key: str = Header(...)):
    if x_update_key != UPDATE_IP_PASS:
        raise HTTPException(status_code=403, detail="Invalid update key")
    
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

@app.get("/health")
async def health_check():
    return JSONResponse(content={"status": "healthy"}, status_code=200)

# Add the check_and_update_api_usage function
async def check_and_update_api_usage(user_id: str, tier: str):
    now = datetime.utcnow()
    today = now.date()
    reset_time = datetime.combine(today, time(hour=0, minute=0)) + timedelta(days=1)
    
    try:
        result = supabase.table("api_usage").select("*").eq("user_id", user_id).eq("date", str(today)).execute()
        
        if not result.data:
            supabase.table("api_usage").insert({"user_id": user_id, "date": str(today), "count": 1}).execute()
            return True, reset_time
        
        usage = result.data[0]
        new_count = usage['count'] + 1
        
        limit = 10 if tier.lower() == "free" else 100 if tier.lower() == "basic" else float('inf')
        
        if new_count > limit:
            return False, reset_time
        
        supabase.table("api_usage").update({"count": new_count}).eq("id", usage['id']).execute()
        return True, reset_time
    except Exception as e:
        logger.error(f"Error in check_and_update_api_usage: {str(e)}")
        return True, reset_time

def ensure_tables_exist():
    try:
        # Check if the api_keys table exists
        api_keys_result = supabase.table("api_keys").select("id").limit(1).execute()
        logger.info(f"api_keys table exists. Result: {api_keys_result}")

        # Check if the api_usage table exists
        api_usage_result = supabase.table("api_usage").select("id").limit(1).execute()
        logger.info(f"api_usage table exists. Result: {api_usage_result}")
    except APIError as e:
        if 'relation "public.api_keys" does not exist' in str(e):
            logger.error("api_keys table does not exist. Please create it manually in your Supabase database.")
            raise ValueError("api_keys table does not exist in the database")
        elif 'relation "public.api_usage" does not exist' in str(e):
            logger.error("api_usage table does not exist. Please create it manually in your Supabase database.")
            raise ValueError("api_usage table does not exist in the database")
        else:
            logger.error(f"Unexpected error when checking tables: {str(e)}")
            raise e

@app.get("/api-usage")
@tier_limit()
async def get_api_usage(api_key_data: dict = Depends(verify_api_key)):
    user_id = api_key_data['user_id']
    tier = api_key_data['tier']
    
    now = datetime.utcnow()
    today = now.date()
    reset_time = datetime.combine(today, time(hour=0, minute=0)) + timedelta(days=1)
    
    result = supabase.table("api_usage").select("*").eq("user_id", user_id).eq("date", str(today)).execute()
    
    if not result.data:
        used_requests = 0
    else:
        used_requests = result.data[0]['count']
    
    limit = 10 if tier.lower() == "free" else 100 if tier.lower() == "basic" else float('inf')
    remaining_requests = max(0, limit - used_requests)
    time_until_reset = reset_time - now
    
    return {
        "tier": tier,
        "used_requests": used_requests,
        "remaining_requests": remaining_requests,
        "reset_in_seconds": time_until_reset.total_seconds(),
        "reset_time": reset_time.isoformat()
    }

if __name__ == "__main__":
    ensure_tables_exist()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))