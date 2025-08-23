from openai import OpenAI
from .setting import setting

class AICLient():
    def __init__(self) -> None:
        # 在初始化时创建并加载设置
        self.settings = setting()
        self.settings.load()
        
        self.system_content: str = """
            You are a helpful assistant Nage. You need to answer user's question based on your knowledge with given json format. There are three possibilities:
            1. Ask to change api_key, endpoint or model, you have to return:
                {
                    "status": "ok",
                    "type": "sett_api",
                    "content": "api key provided by the user",
                    "message": "Your API key are changed."
                }
                {
                    "status": "ok",
                    "type": "sett_ep",
                    "content": "endpoint provided by the user",
                    "message": "Your endpoint are changed."
                }
                {
                    "status": "ok",
                    "type": "sett_md",
                    "content": "model name provided by the user",
                    "message": "Your endpoint are changed."
                }
            2. Ask for answer:
                Please first check if it needs a command, if need a command, just display command in the content, if no, keep it "". If need further info, just ask and keep content "".
                {
                    "status": "ok",
                    "type": "ask",
                    "content": "command to be excute [use this as placeholder], only command, nothing else.",
                    "message": "explanation"
                }
                Most questions are just ask for informations. So maybe just answer the question.
            3. Need to remember:
                {
                    "status": "ok",
                    "type": "memo",
                    "content": "things to be remembered",
                    "message": "ok I will remember that"
                }
            Please strictly follow the format give, if there's exception, just return:
                {
                    "status": "bad",
                    "type": "error",
                    "content": "the problem",
                    "message": "the explanation"
                }
            Please use concise and humorous language(if not been asked for other types).
        """
        self.user_content: str = f"""Hi, this is current memories: {self.settings.load_memo()}. My question is:
        """
        self.client = OpenAI(api_key=self.settings.key, base_url=self.settings.endpoint)

    def request(self,question) -> str:
        response = self.client.chat.completions.create(
            model=self.settings.model,
            messages=[
                {"role": "system", "content": f"{self.system_content}"},
                {"role": "user", "content": f"{self.user_content}{question}"},
            ],
            stream=False
        )
        return str(response.choices[0].message.content)