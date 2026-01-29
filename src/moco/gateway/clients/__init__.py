# -*- coding: utf-8 -*-
"""
Gateway Clients - 外部メッセージングサービスとの連携

各クライアントはスタンドアロンで動作し、moco ui の /api/chat エンドポイントと通信します。

使用方法:
    # WhatsApp
    python -m moco.gateway.clients.whatsapp
    
    # iMessage
    python -m moco.gateway.clients.imessage
    
    # Slack
    python -m moco.gateway.clients.slack

または CLI から:
    moco channel whatsapp
    moco channel imessage
    moco channel slack
"""

# Lazy imports to avoid missing dependency errors
def whatsapp_main():
    from .whatsapp import main
    return main()

def imessage_main():
    from .imessage import main
    return main()

def slack_main():
    from .slack import main
    return main()

__all__ = ["whatsapp_main", "imessage_main", "slack_main"]
