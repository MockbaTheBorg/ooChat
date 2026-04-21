"""API communication for ooChat.

Supports two endpoints:
- Ollama-style: POST /api/chat
- OpenAI-compatible: POST /v1/chat/completions

Both support streaming responses.
"""

import json
import queue
import sys
import threading
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

        if max_tokens is not None:
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

        if max_tokens is not None:
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

        if stream:
            try:
                from . import renderer as renderer_module
            except Exception:
                renderer_module = None
            stream_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
            response_holder: Dict[str, Any] = {}

            def _close_active_response() -> None:
                active_response = response_holder.get("response")
                if active_response is None:
                    return
                try:
                    active_response.close()
                except Exception:
                    pass

            def _stream_worker() -> None:
                pending_tool_calls: Dict[int, Dict[str, Any]] = {}
                response = None
                try:
                    response = requests.post(
                        self.endpoint,
                        json=payload,
                        headers=headers,
                        stream=True,
                        timeout=self.timeout,
                    )
                    response_holder["response"] = response
                    response.raise_for_status()

                    for line in response.iter_lines():
                        if not line:
                            continue
                        try:
                            payload_line = self._decode_stream_line(line)
                            if payload_line is None:
                                continue
                            chunk = json.loads(payload_line)
                            normalized = self._normalize_chunk(chunk)
                            if self.openai_mode:
                                normalized = self._finalize_openai_tool_calls(normalized, pending_tool_calls)
                            if not normalized.get("content") and not normalized.get("tool_calls") and not normalized.get("done"):
                                continue
                            stream_queue.put(("chunk", normalized))
                        except json.JSONDecodeError:
                            continue
                except RequestException as e:
                    stream_queue.put(("error", APIError(f"API request failed: {e}")))
                except Exception as e:
                    stream_queue.put(("error", APIError(f"API request failed: {e}")))
                finally:
                    try:
                        if response is not None:
                            response.close()
                    except Exception:
                        pass
                    stream_queue.put(("done", None))

            worker = threading.Thread(target=_stream_worker, daemon=True)
            try:
                if renderer_module is not None:
                    try:
                        renderer_module.set_spinner_interrupt_callback(_close_active_response)
                    except Exception:
                        pass
                worker.start()

                while True:
                    interrupted = False
                    try:
                        if renderer_module is not None:
                            interrupted = bool(renderer_module.spinner_was_interrupted())
                    except Exception:
                        interrupted = False

                    if interrupted:
                        _close_active_response()
                        break

                    try:
                        item_type, item_payload = stream_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    if item_type == "chunk":
                        yield item_payload
                        continue

                    if item_type == "error":
                        interrupted = False
                        try:
                            if renderer_module is not None:
                                interrupted = bool(renderer_module.spinner_was_interrupted())
                        except Exception:
                            interrupted = False
                        if not interrupted:
                            raise item_payload
                        break

                    if item_type == "done":
                        break
            finally:
                if renderer_module is not None:
                    try:
                        renderer_module.set_spinner_interrupt_callback(None)
                    except Exception:
                        pass
                try:
                    _close_active_response()
                except Exception:
                    pass
                try:
                    worker.join(timeout=0.5)
                except Exception:
                    pass
        else:
            try:
                response = requests.post(
                    self.endpoint,
                    json=payload,
                    headers=headers,
                    stream=False,
                    timeout=self.timeout
                )
                response.raise_for_status()
            except RequestException as e:
                raise APIError(f"API request failed: {e}")
            yield self._normalize_chunk(response.json())

    def _decode_stream_line(self, line: bytes) -> Optional[str]:
        """Decode a streamed line, handling OpenAI-style SSE frames."""
        text = line.decode('utf-8').strip()
        if not text:
            return None

        if text.startswith("data:"):
            text = text[5:].strip()

        if text == "[DONE]":
            return None

        return text

    def _finalize_openai_tool_calls(self, normalized: Dict[str, Any],
                                    pending_tool_calls: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
        """Assemble streamed OpenAI tool call deltas into complete calls.

        OpenAI-compatible streaming delivers tool calls incrementally by index,
        often splitting `id`, `function.name`, and `function.arguments` across
        many chunks. ooChat expects complete calls, so emit them only once the
        server signals the tool-calling turn is done.
        """
        tool_calls = normalized.get("tool_calls")
        if tool_calls:
            for tool_call in tool_calls:
                index = tool_call.get("index", 0)
                entry = pending_tool_calls.setdefault(index, {
                    "id": "",
                    "type": tool_call.get("type", "function"),
                    "function": {
                        "name": "",
                        "arguments": "",
                    },
                })

                if tool_call.get("id"):
                    entry["id"] += tool_call["id"]
                if tool_call.get("type"):
                    entry["type"] = tool_call["type"]

                function = tool_call.get("function", {})
                if function.get("name"):
                    entry["function"]["name"] += function["name"]
                if function.get("arguments"):
                    entry["function"]["arguments"] += function["arguments"]

            normalized["tool_calls"] = None

        if normalized.get("done") and pending_tool_calls:
            finish_reason = normalized.get("finish_reason")
            if finish_reason in ("tool_calls", "stop", None):
                assembled = [
                    pending_tool_calls[index]
                    for index in sorted(pending_tool_calls.keys())
                ]
                normalized["tool_calls"] = assembled
                pending_tool_calls.clear()

        return normalized

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
            message = choice.get("message", {})

            result["content"] = delta.get("content", message.get("content", ""))

            # Check for tool calls in delta
            if "tool_calls" in delta:
                result["tool_calls"] = delta["tool_calls"]
            elif "tool_calls" in message:
                result["tool_calls"] = message["tool_calls"]

            # Check finish reason
            if choice.get("finish_reason"):
                result["done"] = True
                result["finish_reason"] = choice.get("finish_reason")

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