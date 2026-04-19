"""Example skill for ooChat.

This is an example skill that demonstrates how to create
a skill plugin for ooChat.

Skills can:
- Register pre-send filters (modify prompts before sending)
- Register post-receive filters (modify responses before display)
- Register generic functions for use by other skills/commands

Skills CANNOT:
- Register slash commands
- Register shortcuts

To use this skill, place it in the skills/ directory or
pass it via --skill flag.
"""


def register(skill):
    """Register the skill with the chat application.

    Args:
        skill: SkillInterface instance with restricted methods.
    """
    import re

    # Example pre-send filter: normalize whitespace
    def normalize_whitespace(text: str) -> str:
        """Normalize multiple newlines to double newlines."""
        return re.sub(r'\n{3,}', '\n\n', text)

    skill.add_pre_filter(normalize_whitespace)

    # Example post-receive filter: clean up markdown artifacts
    def clean_markdown(text: str) -> str:
        """Clean up common markdown rendering artifacts."""
        # Remove excessive backticks
        text = re.sub(r'```{3,}', '```', text)
        return text

    skill.add_post_filter(clean_markdown)

    # Example generic function
    def count_words(text: str) -> int:
        """Count words in text."""
        return len(text.split())

    skill.add_function('count_words', count_words)

    # Example: access global config
    model = skill.get_global('model')
    print(f"Example skill loaded. Current model: {model}")


# Skills can also provide metadata
SKILL_NAME = "example_skill"
SKILL_VERSION = "1.0.0"
SKILL_DESCRIPTION = "Example skill demonstrating skill plugin API"