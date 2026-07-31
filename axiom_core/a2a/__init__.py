"""A2A (Agent2Agent) 协议支持: 手写轻量客户端。

见 axiom_core.a2a.client (不引入官方 a2a-sdk)。
"""

from axiom_core.a2a.client import A2AClient, A2AError, A2AResult, send_message_streaming

__all__ = ["A2AClient", "A2AError", "A2AResult", "send_message_streaming"]
