def load_text_file(path: str) -> str:
    """Load and return the contents of a text file."""
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()