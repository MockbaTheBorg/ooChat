"""Pre-send and post-receive filter management for ooChat.

Filters allow commands and skills to modify:
- Prompts before sending to the model (pre-send)
- Responses before displaying (post-receive)
"""

from typing import Callable, List, Optional


# Type aliases
FilterFunc = Callable[[str], str]
TextProcessor = Callable[[str, dict], str]


class FilterRegistry:
    """Registry for text filters."""

    def __init__(self):
        """Initialize empty registry."""
        self._pre_send: List[FilterFunc] = []
        self._post_receive: List[FilterFunc] = []

    def register_pre_send(self, func: FilterFunc) -> None:
        """Register a pre-send filter.

        Pre-send filters modify user prompts before sending to the model.

        Args:
            func: Filter function that takes and returns a string.
        """
        if func not in self._pre_send:
            self._pre_send.append(func)

    def unregister_pre_send(self, func: FilterFunc) -> bool:
        """Unregister a pre-send filter.

        Args:
            func: Filter function to remove.

        Returns:
            True if filter was removed.
        """
        try:
            self._pre_send.remove(func)
            return True
        except ValueError:
            return False

    def register_post_receive(self, func: FilterFunc) -> None:
        """Register a post-receive filter.

        Post-receive filters modify assistant responses before display.

        Args:
            func: Filter function that takes and returns a string.
        """
        if func not in self._post_receive:
            self._post_receive.append(func)

    def unregister_post_receive(self, func: FilterFunc) -> bool:
        """Unregister a post-receive filter.

        Args:
            func: Filter function to remove.

        Returns:
            True if filter was removed.
        """
        try:
            self._post_receive.remove(func)
            return True
        except ValueError:
            return False

    def apply_pre_send(self, text: str) -> str:
        """Apply all pre-send filters to text.

        Filters are applied in registration order.

        Args:
            text: Input text.

        Returns:
            Filtered text.
        """
        result = text
        for func in self._pre_send:
            try:
                result = func(result)
            except Exception as e:
                print(f"Warning: Pre-send filter failed: {e}")
        return result

    def apply_post_receive(self, text: str) -> str:
        """Apply all post-receive filters to text.

        Filters are applied in registration order.

        Args:
            text: Input text.

        Returns:
            Filtered text.
        """
        result = text
        for func in self._post_receive:
            try:
                result = func(result)
            except Exception as e:
                print(f"Warning: Post-receive filter failed: {e}")
        return result

    def list_pre_send(self) -> List[str]:
        """List pre-send filter names.

        Returns:
            List of filter function names.
        """
        return [f.__name__ for f in self._pre_send]

    def list_post_receive(self) -> List[str]:
        """List post-receive filter names.

        Returns:
            List of filter function names.
        """
        return [f.__name__ for f in self._post_receive]

    def clear_pre_send(self) -> None:
        """Clear all pre-send filters."""
        self._pre_send.clear()

    def clear_post_receive(self) -> None:
        """Clear all post-receive filters."""
        self._post_receive.clear()

    def clear_all(self) -> None:
        """Clear all filters."""
        self.clear_pre_send()
        self.clear_post_receive()


class HookManager:
    """Manages filter hooks for the chat application.

    This provides a higher-level interface for hooks that need
    access to the chat context.
    """

    def __init__(self):
        """Initialize hook manager."""
        self._pre_hooks: List[TextProcessor] = []
        self._post_hooks: List[TextProcessor] = []

    def add_pre_hook(self, hook: TextProcessor) -> None:
        """Add a pre-send hook.

        Pre-send hooks receive (text, context) and return modified text.

        Args:
            hook: Hook function.
        """
        if hook not in self._pre_hooks:
            self._pre_hooks.append(hook)

    def add_post_hook(self, hook: TextProcessor) -> None:
        """Add a post-receive hook.

        Post-receive hooks receive (text, context) and return modified text.

        Args:
            hook: Hook function.
        """
        if hook not in self._post_hooks:
            self._post_hooks.append(hook)

    def run_pre_hooks(self, text: str, context: dict) -> str:
        """Run all pre-send hooks.

        Args:
            text: Input text.
            context: Context dictionary (model, session info, etc.).

        Returns:
            Modified text.
        """
        result = text
        for hook in self._pre_hooks:
            try:
                result = hook(result, context)
            except Exception as e:
                print(f"Warning: Pre-hook failed: {e}")
        return result

    def run_post_hooks(self, text: str, context: dict) -> str:
        """Run all post-receive hooks.

        Args:
            text: Input text.
            context: Context dictionary.

        Returns:
            Modified text.
        """
        result = text
        for hook in self._post_hooks:
            try:
                result = hook(result, context)
            except Exception as e:
                print(f"Warning: Post-hook failed: {e}")
        return result


# Example filter functions
def trim_whitespace(text: str) -> str:
    """Trim leading/trailing whitespace from text.

    Args:
        text: Input text.

    Returns:
        Trimmed text.
    """
    return text.strip()


def normalize_newlines(text: str) -> str:
    """Normalize line endings to Unix style.

    Args:
        text: Input text.

    Returns:
        Text with Unix line endings.
    """
    return text.replace('\r\n', '\n').replace('\r', '\n')


def create_prefix_filter(prefix: str) -> FilterFunc:
    """Create a filter that adds a prefix to text.

    Args:
        prefix: Prefix to add.

    Returns:
        Filter function.
    """
    def filter_func(text: str) -> str:
        return f"{prefix}{text}"
    return filter_func


def create_suffix_filter(suffix: str) -> FilterFunc:
    """Create a filter that adds a suffix to text.

    Args:
        suffix: Suffix to add.

    Returns:
        Filter function.
    """
    def filter_func(text: str) -> str:
        return f"{text}{suffix}"
    return filter_func


def create_replace_filter(old: str, new: str) -> FilterFunc:
    """Create a filter that replaces text.

    Args:
        old: Text to replace.
        new: Replacement text.

    Returns:
        Filter function.
    """
    def filter_func(text: str) -> str:
        return text.replace(old, new)
    return filter_func


def create_regex_filter(pattern, replacement) -> FilterFunc:
    """Create a filter using regex substitution.

    Args:
        pattern: Regex pattern (string or compiled).
        replacement: Replacement string.

    Returns:
        Filter function.
    """
    import re
    if isinstance(pattern, str):
        pattern = re.compile(pattern)

    def filter_func(text: str) -> str:
        return pattern.sub(replacement, text)
    return filter_func