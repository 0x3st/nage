import json


class JsonParser:
    def __init__(self, json_str) -> None:
        self.json_str = json.loads(json_str)

    def check_status(self):
        # New format doesn't include status field, if type field exists then it's valid
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
            
    def read_clear_history(self):
        """Read clear_histroy field to determine if history should be cleared."""
        if self.check_status():
            return self.json_str.get("clear_history", False)
        else:
            return False
        
    def read_clear_memory(self):
        """Read clear_memory field to determine if memory should be cleared."""
        if self.check_status():
            return self.json_str.get("clear_memory", False)
        else:
            return False