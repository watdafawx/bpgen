"""Prototype data from `factorio --dump-data`."""
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Data:
    raw: dict

    @classmethod
    def load(cls, path):
        """the dump at path; the planner follows it from now on (its pole and pipes: planner.configure)"""
        data = cls(json.loads(Path(path).read_text(encoding="utf-8")))
        from bpgen import planner  # (local: planner imports half of bpgen)
        planner.configure(data)
        return data
