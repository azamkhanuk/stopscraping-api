# stopscraping

## API Documentation

### Authentication

All endpoints require an API key to be sent in the header of each request.

Header: X-API-Key: your_api_key_here

### Endpoints

#### 1. Get Block IPs

Retrieves the list of blocked IP ranges for OpenAI bots.

Endpoint: GET /block-ips

cURL Example:
curl -X GET https://api.example.com/block-ips \
  -H "X-API-Key: your_api_key_here"

REST Example:
GET /block-ips HTTP/1.1
Host: api.example.com
X-API-Key: your_api_key_here

Example Response:
{
  "openai": {
    "searchbot": ["192.168.1.1/24"],
    "chatgpt-user": ["10.0.0.1/24"],
    "gptbot": ["172.16.0.1/24"]
  }
}

#### 2. Get Bot IPs

Retrieves the list of blocked IP ranges for a specific OpenAI bot type.

Endpoint: GET /block-ips/{bot_type}

Parameters:
- bot_type: The type of bot (e.g., "searchbot", "chatgpt-user", "gptbot")

cURL Example:
curl -X GET https://api.example.com/block-ips/searchbot \
  -H "X-API-Key: your_api_key_here"

REST Example:
GET /block-ips/searchbot HTTP/1.1
Host: api.example.com
X-API-Key: your_api_key_here

Example Response:
{
  "searchbot": ["192.168.1.1/24"]
}

#### 3. Get API Usage

Retrieves the current API usage statistics for the authenticated user.

Endpoint: GET /api-usage

cURL Example:
curl -X GET https://api.example.com/api-usage \
  -H "X-API-Key: your_api_key_here"

REST Example:
GET /api-usage HTTP/1.1
Host: api.example.com
X-API-Key: your_api_key_here

Example Response:
{
  "tier": "basic",
  "used_requests": 45,
  "remaining_requests": 55,
  "reset_in_seconds": 14400,
  "reset_time": "2023-04-15T00:00:00"
}

### Rate Limiting

The API is subject to rate limiting based on your account tier:
- Free tier: 10 requests per day
- Basic tier: 100 requests per day
- Higher tiers: Unlimited requests

If you exceed your rate limit, you'll receive a 429 error with information about when the limit will reset.

### Error Responses

In case of errors, the API will return appropriate HTTP status codes along with error details in the response body.

Example error response:
{
  "detail": "API call limit exceeded. Resets in 2 hours, 30 minutes."
}

For any issues or questions about the API, please contact our support team.
