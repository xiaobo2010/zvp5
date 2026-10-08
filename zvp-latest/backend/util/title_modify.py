import re


def modify_title_automatically(title: str):
    """
    Automatically modifies a title to ensure it meets the specified requirements:
    1. Contains only Chinese, Latin Characters, numbers, spaces, slashes, and dots.
    2. ASCII letters are wrapped with spaces if inserted between Chinese characters.

    Args:
        title (str): The input title string.

    Returns:
        str: The modified title string.
    """
    modified_title = title

    # Requirement 2: Ensure ASCII letters are wrapped with spaces if between Chinese characters
    # Add space before ASCII char if preceded by Chinese
    modified_title = re.sub(r"([\u4e00-\u9fa5])([a-zA-Z0-9])", r"\1 \2", modified_title)
    # Add space after ASCII char if followed by Chinese
    modified_title = re.sub(r"([a-zA-Z0-9])([\u4e00-\u9fa5])", r"\1 \2", modified_title)

    # Requirement 1: Filter out invalid characters
    # Allow Chinese, Latin Characters, numbers, spaces, slashes, dashes (en dash, em dash, hyphen), and dots
    # The regex for allowed characters is based on your provided validation regex.
    # Note: \u2013 is en dash, \u2014 is em dash. '-' is hyphen.
    allowed_chars_regex = r"[^\u4e00-\u9fa5\u2013\u2014a-zA-Z0-9 /.-]"
    modified_title = re.sub(allowed_chars_regex, "", modified_title)

    # Clean up any extra spaces that might have been introduced or accumulated
    modified_title = re.sub(r"\s+", " ", modified_title).strip()

    return modified_title

