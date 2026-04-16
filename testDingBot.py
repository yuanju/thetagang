from dingtalkchatbot.chatbot import DingtalkChatbot
import time

# ================= 配置区域 =================
# 替换为你的 Webhook 地址 (access_token=...)
WEBHOOK_URL = 'https://oapi.dingtalk.com/robot/send?access_token=536a2af7274eee3d93a1055a39d856b686224f0044f6b7fd3a97dd45fe997f20'

# 替换为你的安全设置秘钥 (如果开启了"加签"，必须以 SEC 开头)
# 如果未开启加签，此处填 None 即可
SECRET = 'SECb537c0c4a194975e9b24e313b17d08096f1e547de749212a90ce0d5361ed2965' 
# ===========================================

def main():
    # 初始化机器人
    # 如果开启了加签，必须传入 secret 参数，否则签名会失败
    chatbot = DingtalkChatbot(WEBHOOK_URL, secret=SECRET)

    print("开始发送测试消息...")

    # --- 示例 1: 发送普通文本消息 ---
    try:
        chatbot.send_text(
            msg='你好，这是一条测试文本消息！\n当前时间：' + time.strftime("%Y-%m-%d %H:%M:%S"),
            is_at_all=True  # True: @所有人; False: 不@任何人
        )
        print("✓ 文本消息发送成功")
    except Exception as e:
        print(f"✗ 文本消息发送失败: {e}")

    # --- 示例 2: 发送 Markdown 消息 (推荐用于报警/日志) ---
    try:
        md_content = """## 🚨 服务器报警通知
- **服务**: 订单服务
- **状态**: <font color="#FF0000">异常</font>
- **错误码**: 503 Service Unavailable
- **详情**: 数据库连接超时，请立刻检查！
- [点击查看监控面板](http://example.com)
        """
        chatbot.send_markdown(
            title="服务器报警", 
            text=md_content,
            is_at_all=False,
            # at_mobiles=["13800138000"] # 可选：@指定手机号的人
        )
        print("✓ Markdown 消息发送成功")
    except Exception as e:
        print(f"✗ Markdown 消息发送失败: {e}")

    # --- 示例 3: 发送链接消息 ---
    try:
        chatbot.send_link(
            title="📢 新版本发布通知",
            text="v2.0.1 版本已上线，修复了若干已知问题，欢迎体验！",
            message_url="http://example.com/release-notes",
            pic_url="https://img.alicdn.com/tfs/TB1NwmBEL9TBuNjy1zbXXXpepXa-241-41.png" # 可选：缩略图
        )
        print("✓ 链接消息发送成功")
    except Exception as e:
        print(f"✗ 链接消息发送失败: {e}")

    # --- 示例 4: 发送 ActionCard 卡片消息 (带按钮) ---
    try:
        chatbot.send_action_card(
            title="审批请求",
            text="张三 提交了一个请假申请。\n事由：身体不适，请假1天。",
            btn_orientation="1", # 0: 按钮竖直排列，1: 按钮水平排列
            btns=[
                {"title": "同意", "action_url": "http://example.com/approve"},
                {"title": "拒绝", "action_url": "http://example.com/reject"}
            ]
        )
        print("✓ 卡片消息发送成功")
    except Exception as e:
        print(f"✗ 卡片消息发送失败: {e}")

if __name__ == '__main__':
    main()
