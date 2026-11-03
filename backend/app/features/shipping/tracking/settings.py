"""快递100只读查询配置：凭证只留在后端，未配置时不发起外部请求。"""
import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TrackingSettings:
    """不可变的本次配置快照；凭证排除在 repr 外，避免调试输出时意外暴露。"""
    customer: str = field(default="", repr=False)
    key: str = field(default="", repr=False)
    auto_sync: bool = False
    interval_minutes: int = 30
    carrier_codes: dict[str, str] = field(default_factory=dict)

    @property
    def configured(self):
        return bool(self.customer and self.key)

    @classmethod
    def from_env(cls):
        """读取启动进程的环境配置；非法数字和无效公司映射回退为可用默认值。"""
        def number(name, default, minimum):
            try:
                return max(minimum, int(os.getenv(name, str(default))))
            except ValueError:
                return default
        try:
            codes = json.loads(os.getenv("KD100_CARRIER_CODES", "{}"))
            codes = codes if isinstance(codes, dict) else {}
        except ValueError:
            codes = {}
        return cls(
            customer=os.getenv("KD100_CUSTOMER", "").strip(), key=os.getenv("KD100_KEY", "").strip(),
            auto_sync=os.getenv("LOGISTICS_AUTO_SYNC", "false").lower() in {"1", "true", "yes"},
            # 官方要求同单至少间隔半小时；环境配置也不能绕过这个下限。
            interval_minutes=number("LOGISTICS_SYNC_INTERVAL_MINUTES", 30, 30),
            carrier_codes={str(k): str(v) for k, v in codes.items()},
        )

    def public(self):
        """仅返回查询开关和同步间隔，不包含查询凭证。"""
        return {"provider": "kuaidi100", "provider_name": "快递100", "configured": self.configured,
                "auto_sync": self.configured and self.auto_sync, "interval_minutes": self.interval_minutes}
