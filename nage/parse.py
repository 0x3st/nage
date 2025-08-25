import json

class ParseJSON():
    def __init__(self, json_str) -> None:
        self.json_str=json.loads(json_str)

    def check_status(self):
        # 新格式不包含 status 字段，如果有 type 字段就认为是有效的
        return "type" in self.json_str
    
    def read_type(self):
        if self.check_status():
            return self.json_str.get("type", "")
        else:
            return ""
        
    def read_content(self):
        if self.check_status():
            return self.json_str.get("content", "")
        else:
            return ""
        
    def read_msg(self):
        if self.check_status():
            return self.json_str.get("message", "")
        else:
            return ""
            
    def read_clear(self):
        """读取 clear 字段，用于判断是否需要清空历史记录"""
        if self.check_status():
            return self.json_str.get("clear", False)
        else:
            return False