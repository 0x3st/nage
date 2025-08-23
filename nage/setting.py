import os
import json
from pathlib import Path

class setting():
    def __init__(self, model:str = "deepseek-chat", 
                 api_key:str ="", 
                 endpoint:str="https://api.deepseek.com/v1") -> None:
        self.model = model
        self.key = api_key
        self.endpoint = endpoint
        self.home_dir = Path.home() / ".nage"
        self.sett_file = self.home_dir / "SETT"
        self.memo_file = self.home_dir / "MEMO"
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
        """保存记忆内容到 MEMO 文件，memo_list 为字符串列表"""
        with open(self.memo_file, "w", encoding="utf-8") as f:
            json.dump(memo_list, f)

    def load_memo(self):
        """加载 MEMO 文件内容，返回字符串列表"""
        if self.memo_file.exists():
            with open(self.memo_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def add_memo(self, memo_item):
        """添加一条记忆内容到 MEMO 文件"""
        memos = self.load_memo()
        memos.append(memo_item)
        self.save_memo(memos)

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