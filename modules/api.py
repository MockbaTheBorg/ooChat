"""API communication for ooChat.

Supports two endpoints:
- Ollama-style: POST /api/chat
- OpenAI-compatible: POST /v1/chat/completions

Both support streaming responses.
"""

import json
import sys
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests
from requests.exceptions import RequestException

from . import globals as globals_module


class APIError(Exception):
    """API communication error."""
    pass


class APIClient:
    """HTTP client for chat API."""

    def __init__(self, host: str = None, port: int = None, openai_mode: bool = None):
        """Initialize API client.

        Args:
            host: API host. Defaults to GLOBALS['host'].
            port: API port. Defaults to GLOBALS['port'].
            openai_mode: Use OpenAI-compatible endpoint. Defaults to GLOBALS['openai_mode'].
        """
        self.host = host or globals_module.GLOBALS.get('host', 'localhost')
        self.port = port or globals_module.GLOBALS.get('port', 11434)
        self.openai_mode = openai_mode if openai_mode is not None else globals_module.GLOBALS.get('openai_mode', False)
        self.timeout = globals_module.GLOBALS.get('request_timeout', 300)

    @property
    def base_url(self) -> str:
        """Get the base URL for the API."""
        return f"http://{self.host}:{self.port}"

    @property
    def endpoint(self) -> str:
        """Get the chat endpoint based on mode."""
        if self.openai_mode:
            return f"{self.base_url}/v1/chat/completions"
        return f"{self.base_url}/api/chat"

    def build_ollama_request(self, model: str, messages: List[Dict[str, str]],
                              stream: bool = True, tools: List[Dict] = None,
                              max_tokens: int = None) -> Dict[str, Any]:
        """Build an Ollama-style request payload.

        Args:
            model: Model name.
            messages: List of message dicts with 'role' and 'content'.
            stream: Whether to stream the response.
            tools: Optional list of tool definitions.
            max_tokens: Maximum tokens to generate.

        Returns:
            Request payload dictionary.
        """
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }

        if tools:
            payload["tools"] = tools

        if max_tokens:
            payload["options"] = {"num_predict": max_tokens}

        return payload

    def build_openai_request(self, model: str, messages: List[Dict[str, str]],
                              stream: bool = True, tools: List[Dict] = None,
                              max_tokens: int = None) -> Dict[str, Any]:
        """Build an OpenAI-compatible request payload.

        Args:
            model: Model name.
            messages: List of message dicts with 'role' and 'content'.
            stream: Whether to stream the response.
            tools: Optional list of tool definitions.
            max_tokens: Maximum tokens to generate.

        Returns:
            Request payload dictionary.
        """
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }

        if tools:
            payload["tools"] = tools

        if max_tokens:
            payload["max_tokens"] = max_tokens

        return payload

    def chat(self, model: str, messages: List[Dict[str, str]],
             stream: bool = True, tools: List[Dict] = None,
             max_tokens: int = None) -> Generator[Dict[str, Any], None, None]:
        """Send a chat request and yield response chunks.

        Args:
            model: Model name.
            messages: List of message dicts.
            stream: Whether to stream the response.
            tools: Optional list of tool definitions.
            max_tokens: Maximum tokens to generate.

        Yields:
            Response chunk dictionaries.

        Raises:
            APIError: If the request fails.
        """
        if self.openai_mode:
            payload = self.build_openai_request(model, messages, stream, tools, max_tokens)
        else:
            payload = self.build_ollama_request(model, messages, stream, tools, max_tokens)

        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(
                self.endpoint,
                json=payload,
                headers=headers,
                stream=stream,
                timeout=self.timeout
            )
            response.raise_for_status()
        except RequestException as e:
            raise APIError(f"API request failed: {e}")

        if stream:
            for line in response.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line.decode('utf-8'))
                        yield self._normalize_chunk(chunk)
                    except json.JSONDecodeError as e:
                        # Skip malformed lines
                        continue
        else:
            yield self._normalize_chunk(response.json())

    def _normalize_chunk(self, chunk: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a response chunk to a common format.

        Args:
            chunk: Raw response chunk.

        Returns:
            Normalized chunk with 'content', 'done', 'tool_calls' fields.
        """
        if self.openai_mode:
            return self._normalize_openai_chunk(chunk)
        return self._normalize_ollama_chunk(chunk)

    def _normalize_ollama_chunk(self, chunk: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize an Ollama-style response chunk.

        Args:
            chunk: Ollama response chunk.

        Returns:
            Normalized chunk.
        """
        result = {
            "content": "",
            "done": False,
            "tool_calls": None,
        }

        # Content
        if "message" in chunk:
            result["content"] = chunk["message"].get("content", "")
            if "tool_calls" in chunk["message"]:
                result["tool_calls"] = chunk["message"]["tool_calls"]

        # Done flag
        if chunk.get("done", False):
            result["done"] = True
            result["total_duration"] = chunk.get("total_duration")
            result["eval_count"] = chunk.get("eval_count")

        return result

    def _normalize_openai_chunk(self, chunk: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize an OpenAI-compatible response chunk.

        Args:
            chunk: OpenAI response chunk.

        Returns:
            Normalized chunk.
        """
        result = {
            "content": "",
            "done": False,
            "tool_calls": None,
        }

        # Handle streaming chunk
        if "choices" in chunk and len(chunk["choices"]) > 0:
            choice = chunk["choices"][0]
            delta = choice.get("delta", {})

            result["content"] = delta.get("content", "")

            # Check for tool calls in delta
            if "tool_calls" in delta:
                result["tool_calls"] = delta["tool_calls"]

            # Check finish reason
            if choice.get("finish_reason"):
                result["done"] = True

        # Handle non-streaming response
        elif "choices" in chunk:
            choice = chunk["choices"][0]
            message = choice.get("message", {})
            result["content"] = message.get("content", "")
            result["done"] = True
            if "tool_calls" in message:
                result["tool_calls"] = message["tool_calls"]

        return result

    def list_models(self) -> List[Dict[str, Any]]:
        """List available models from the API.

        Returns:
            List of model info dictionaries.

        Raises:
            APIError: If the request fails.
        """
        # Try Ollama-style endpoint first
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=self.timeout)
            if response.status_code == 200:
                data = response.json()
                return data.get("models", [])
        except RequestException:
            pass

        # Try OpenAI-compatible endpoint
        try:
            response = requests.get(f"{self.base_url}/v1/models", timeout=self.timeout)
            if response.status_code == 200:
                data = response.json()
                models = []
                for model in data.get("data", []):
                    models.append({
                        "name": model.get("id", "unknown"),
                        "id": model.get("id", "unknown"),
                    })
                return models
        except RequestException:
            pass

        return []

    def test_connection(self) -> Tuple[bool, str]:
        """Test the API connection.

        Returns:
            Tuple of (success, message).
        """
        try:
            response = requests.get(f"{self.base_url}/", timeout=5)
            return True, f"Connected to {self.base_url}"
        except RequestException as e:
            return False, f"Connection failed: {e}"


def send_chat(model: str, messages: List[Dict[str, str]],
              stream: bool = True, tools: List[Dict] = None,
              max_tokens: int = None, **kwargs) -> Generator[Dict[str, Any], None, None]:
    """Send a chat request using global config.

    Args:
        model: Model name.
        messages: List of message dicts.
        stream: Whether to stream the response.
        tools: Optional list of tool definitions.
        max_tokens: Maximum tokens to generate.
        **kwargs: Additional arguments for APIClient.

    Yields:
        Response chunk dictionaries.
    """
    client = APIClient(**kwargs)
    yield from client.chat(model, messages, stream, tools, max_tokens)