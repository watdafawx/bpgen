"""Prototype data from `factorio --dump-data`."""
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Data:
    raw: dict

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))
