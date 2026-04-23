#!/usr/bin/env python3
"""Fetch the content of a web page, following redirects and handling errors."""

import json
import sys
import traceback
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print(json.dumps({
        "error": True,
        "message": "The 'requests' library is not installed. Please install it with: pip install requests"
    }))
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


def fetch_page(url, max_length=32768, source=False):
    """
    Fetch the content from a URL, following redirects.

    Returns:
        dict with keys: 'success', 'url' (final URL), 'content', 'status_code', 'message'
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; WebScrapeBot/1.0; +https://example.com/bot)"
        )
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=15,
            allow_redirects=True,
        )
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "url": url,
            "status_code": None,
            "message": f"Error: Request timed out after 15 seconds for URL: {url}"
        }
    except requests.exceptions.ConnectionError as e:
        return {
            "success": False,
            "url": url,
            "status_code": None,
            "message": f"Error: Could not connect to the server at {url}. Details: {e}"
        }
    except requests.exceptions.TooManyRedirects:
        return {
            "success": False,
            "url": url,
            "status_code": response.status_code if hasattr(response, 'status_code') else None,
            "message": f"Error: Too many redirects encountered for URL: {url}"
        }
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "url": url,
            "status_code": response.status_code if hasattr(response, 'status_code') else None,
            "message": f"Error: A request error occurred for URL: {url}. Details: {e}"
        }

    # Check for HTTP error status codes
    if response.status_code == 404:
        return {
            "success": False,
            "url": response.url,
            "status_code": 404,
            "message": f"Error: 404 Not Found. The page at URL {response.url} was not found."
        }
    elif response.status_code == 403:
        return {
            "success": False,
            "url": response.url,
            "status_code": 403,
            "message": f"Error: 403 Forbidden. Access to {response.url} is forbidden."
        }
    elif response.status_code == 401:
        return {
            "success": False,
            "url": response.url,
            "status_code": 401,
            "message": f"Error: 401 Unauthorized. Authentication is required for {response.url}."
        }
    elif response.status_code == 500:
        return {
            "success": False,
            "url": response.url,
            "status_code": 500,
            "message": f"Error: 500 Internal Server Error. The server encountered an error at {response.url}."
        }
    elif response.status_code == 502:
        return {
            "success": False,
            "url": response.url,
            "status_code": 502,
            "message": f"Error: 502 Bad Gateway. Received an invalid response from {response.url}."
        }
    elif response.status_code == 503:
        return {
            "success": False,
            "url": response.url,
            "status_code": 503,
            "message": f"Error: 503 Service Unavailable. {response.url} is currently unavailable."
        }
    elif response.status_code == 429:
        return {
            "success": False,
            "url": response.url,
            "status_code": 429,
            "message": f"Error: 429 Too Many Requests. Rate limit exceeded for {response.url}."
        }
    elif response.status_code >= 400:
        return {
            "success": False,
            "url": response.url,
            "status_code": response.status_code,
            "message": f"Error: HTTP {response.status_code}. Failed to fetch {response.url}."
        }

    # Success
    raw = response.text

    # Return raw source if requested
    if source:
        content = raw
    else:
        # Use BeautifulSoup to extract text if available
        if HAS_BS4:
            try:
                soup = BeautifulSoup(raw, "html.parser")
                # Remove script and style elements
                for element in soup(["script", "style", "noscript", "header", "footer", "nav"]):
                    element.decompose()
                content = soup.get_text(separator="\n", strip=True)
            except Exception:
                content = raw
        else:
            content = raw

    # Truncate if necessary
    if len(content) > max_length:
        content = content[:max_length] + "\n\n[Content truncated due to length.]"

    return {
        "success": True,
        "url": response.url,
        "status_code": response.status_code,
        "content": content,
    }


def main():
    # Read parameters from stdin
    input_data = sys.stdin.read()
    try:
        params = json.loads(input_data)
    except json.JSONDecodeError:
        print(json.dumps({
            "success": False,
            "message": "Error: Invalid JSON input. Expected a JSON object with a 'url' parameter."
        }))
        sys.exit(1)

    url = params.get("url")
    if not url:
        print(json.dumps({
            "success": False,
            "message": "Error: The 'url' parameter is required."
        }))
        sys.exit(1)

    # Basic URL validation
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        print(json.dumps({
            "success": False,
            "message": f"Error: Invalid URL format. Please provide a valid URL with a scheme (e.g., http:// or https://). Got: {url}"
        }))
        sys.exit(1)

    max_length = params.get("max_length", 32768)
    source = params.get("source", False)
    if isinstance(source, str):
        source = source.lower() in ("1", "true", "yes", "y")

    # Fetch the page
    result = fetch_page(url, max_length, source)

    # Output as JSON
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({
            "success": False,
            "message": f"Unexpected error: {str(e)}\n{traceback.format_exc()}"
        }))
        sys.exit(1)
