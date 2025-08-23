import json

class ParseJSON():
    def __init__(self, json_str) -> None:
        self.json_str=json.loads(json_str)

    def check_status(self):
        if self.json_str["status"] == "ok":
            return True
        else:
            return False
    
    def read_type(self):
        if self.check_status():
            return self.json_str["type"]
        else:
            return ""
        
    def read_content(self):
        if self.check_status():
            return self.json_str["content"]
        else:
            return ""
        
    def read_msg(self):
        if self.check_status():
            return self.json_str["message"]
        else:
            return ""