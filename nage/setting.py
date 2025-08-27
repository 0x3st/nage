import os
import json
from pathlib import Path


class Setting:
    def __init__(self, model: str = "deepseek-chat",
                 api_key: str = "",
                 endpoint: str = "https://api.deepseek.com/v1") -> None:
        self.model = model
        self.key = api_key
        self.endpoint = endpoint
        self.home_dir = Path.home() / ".nage"
        self.sett_file = self.home_dir / "SETT"
        self.memo_file = self.home_dir / "MEMO"
        self.history_file = self.home_dir / "HIST"
        self._ensure_dir()

    def _ensure_dir(self):
        if not self.home_dir.exists():
            self.home_dir.mkdir(parents=True, exist_ok=True)

    def save(self):
        data = {
            "model": self.model,
            "key": self.key,
            "endpoint": self.endpoint
        }
        with open(self.sett_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def save_memo(self, memo_list):
        """Save memory content to MEMO file, memo_list is a list of strings."""
        with open(self.memo_file, "w", encoding="utf-8") as f:
            json.dump(memo_list, f)

    def save_history(self, history_list):
        """Save history content to HIST file, history_list is a list of strings."""
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(history_list, f)

    def load_memo(self):
        """Load MEMO file content, returns a list of strings."""
        if self.memo_file.exists():
            with open(self.memo_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def load_history(self):
        """Load HIST file content, returns a list of strings."""
        if self.history_file.exists():
            with open(self.history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def add_memo(self, memo_item):
        """Add a memory item to MEMO file."""
        memos = self.load_memo()
        memos.append(memo_item)
        self.save_memo(memos)

    def add_history(self, history_item):
        """Add a history item to HIST file."""
        history = self.load_history()
        history.append(history_item)
        self.save_history(history)

    def clear_history(self):
        """Clear history records."""
        self.save_history([])

    def clear_memo(self):
        """Clear memory content."""
        self.save_memo([])

    def load(self):
        if self.sett_file.exists():
            with open(self.sett_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.model = data.get("model", self.model)
            self.key = data.get("key", self.key)
            self.endpoint = data.get("endpoint", self.endpoint)
            return True
        return False

    def change_key(self, new_api_key) -> str:
        self.key = new_api_key
        return self.key
    
    def change_model(self, new_model_name) -> str:
        self.model = new_model_name
        return self.model

    def change_endpoint(self, new_endpoint) -> str:
        self.endpoint = new_endpoint
        return self.endpoint