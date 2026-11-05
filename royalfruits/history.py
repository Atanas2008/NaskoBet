"""GameHistory: audit log of every round (stops, grids, wins). Optional JSONL file."""
import json
import time


class GameHistory:
    def __init__(self, path=None):
        self.rounds = []
        self.path = path

    def record(self, result, **extra) -> dict:
        entry = {"round": len(self.rounds) + 1, "time": time.time(), **result.to_dict(), **extra}
        self.rounds.append(entry)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        return entry
