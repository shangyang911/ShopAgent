"""只在服务端读取百炼凭证；未配置凭证时应用仍可启动。"""
import os
from dataclasses import dataclass

from app.core import config as _config  # noqa: F401 - load backend/.env first


@dataclass(frozen=True)
class AgentSettings:
    api_key: str
    base_url: str
    ws_url: str
    model: str
    asr_model: str
    tts_model: str
    voice: str
    rag_url: str
    knowledge_base_id: str
    allowed_origins: tuple[str, ...]
    knowledge_source: str = 'local'

    @classmethod
    def from_env(cls):
        workspace = os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip()
        region = os.getenv("DASHSCOPE_REGION", "cn-beijing").strip()
        public_host = "dashscope-intl.aliyuncs.com" if region == "ap-southeast-1" else "dashscope.aliyuncs.com"
        host = f"{workspace}.{region}.maas.aliyuncs.com" if workspace else public_host
        return cls(
            api_key=(os.getenv("DASHSCOPE_API_KEY") or os.getenv("BAILIAN_API_KEY") or "").strip(),
            base_url=os.getenv("DASHSCOPE_BASE_URL", f"https://{public_host}/compatible-mode/v1").rstrip("/"),
            ws_url=os.getenv("DASHSCOPE_WS_URL", f"wss://{host}/api-ws/v1/inference"),
            model=os.getenv("AGENT_MODEL", "qwen-plus"),
            asr_model=os.getenv("AGENT_ASR_MODEL", "fun-asr-realtime"),
            tts_model=os.getenv("AGENT_TTS_MODEL", "cosyvoice-v3-flash"),
            voice=os.getenv("AGENT_TTS_VOICE", "longanyang"),
            rag_url=os.getenv("BAILIAN_RAG_URL", f"https://{host}/api/v1/indices/rag/index/retrieve" if workspace else ""),
            knowledge_base_id=os.getenv("BAILIAN_KNOWLEDGE_BASE_ID", "").strip(),
            allowed_origins=tuple(x.strip().rstrip("/") for x in os.getenv(
                "AGENT_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",") if x.strip()),
            knowledge_source=os.getenv('AGENT_KNOWLEDGE_SOURCE', 'local').strip(),
        )

    def capabilities(self):
        return {
            "chat": bool(self.api_key), "voice_input": bool(self.api_key),
            "voice_output": bool(self.api_key),
            "knowledge_base": self.knowledge_source == 'local' or bool(self.api_key and self.rag_url and self.knowledge_base_id),
            "business_tools": ["search_orders", "get_order", "get_logistics", "search_products", "prepare_cancel_order",
                               "analyze_replenishment", "analyze_shipping_backlog", "search_knowledge", "create_handoff_ticket"],
            "handoff": "ticket", "identity": "administrator",
        }
