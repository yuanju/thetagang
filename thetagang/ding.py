from dingtalkchatbot.chatbot import DingtalkChatbot
import time



WEBHOOK_URL = 'https://oapi.dingtalk.com/robot/send?access_token=536a2af7274eee3d93a1055a39d856b686224f0044f6b7fd3a97dd45fe997f20'
SECRET = 'SECb537c0c4a194975e9b24e313b17d08096f1e547de749212a90ce0d5361ed2965'

def send_markdown(mardown:str):
    chatbot = DingtalkChatbot(WEBHOOK_URL, secret=SECRET)
    chatbot.send_markdown(
        title="thetagang交易动态",
        text=mardown,
        is_at_all=False,
        # at_mobiles=["13800138000"] # 可选：@指定手机号的人
    )
