__all__ = ('calculate_hash', 'get_real_key')

import hashlib
from pathlib import Path
from typing import Optional


def calculate_hash(file_path: Path, algo="sha1") -> Optional[str]:
    """Calculate the cryptographic hash of a file."""
    if not file_path.exists():
        return None
    if algo not in hashlib.algorithms_available:
        return None
    h = hashlib.new(algo)
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def get_real_key(config_deps: dict, name: str) -> Optional[str]:
    """Perform a case-insensitive search for a key in a dictionary."""
    target = name.casefold()
    for key in config_deps:
        if key.casefold() == target:
            return key
    return None
