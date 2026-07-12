import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def load_text_file(path: str) -> str:
    """Load and return the contents of a text file."""
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _example_path(path: Path) -> Path:
    """Return the .example sibling for a working file.

    templates/renter_profile.txt -> templates/renter_profile.example.txt
    """
    return path.with_suffix('.example' + path.suffix)


def ensure_working_file(path: str) -> str:
    """Return `path`, creating it from its `.example` sibling if it's missing.

    Personal working files (renter_profile.txt, application_template.txt, ...)
    are gitignored, so a fresh clone won't have them. Rather than crash, seed the
    file from its committed `.example` template and warn loudly that it holds
    placeholder text the user must edit. Raises FileNotFoundError only if neither
    the working file nor its example exists.
    """
    p = Path(path)
    if p.exists():
        return path

    example = _example_path(p)
    if example.exists():
        shutil.copyfile(example, p)
        logger.warning(
            f"{p} was missing — created it from {example.name} with PLACEHOLDER "
            f"content. Edit {p} with your real details for good results."
        )
        return path

    raise FileNotFoundError(
        f"{p} not found and no template ({example}) to fall back to. "
        f"Run `python setup.py` to generate your config files."
    )
