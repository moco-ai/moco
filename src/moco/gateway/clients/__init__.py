# -*- coding: utf-8 -*-
"""
Gateway Clients - 外部メッセージングサービスとの連携

各クライアントはスタンドアロンで動作し、moco ui の /api/chat エンドポイントと通信します。

使用方法:
    # WhatsApp
    python -m moco.gateway.clients.whatsapp
    
    # iMessage
    python -m moco.gateway.clients.imessage

または CLI から:
    moco channel whatsapp
    moco channel imessage
"""

from .whatsapp import main as whatsapp_main
from .imessage import main as imessage_main

__all__ = ["whatsapp_main", "imessage_main"]
