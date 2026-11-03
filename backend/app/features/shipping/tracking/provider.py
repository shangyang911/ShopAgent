"""快递100实时查询适配器。依据官方 query.do 文档签名，原始凭证和响应不写日志。"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import md5, sha256

import httpx


SHANGHAI = timezone(timedelta(hours=8))
CARRIER_CODES = {
    "顺丰速运": "shunfeng", "顺丰": "shunfeng", "中通快递": "zhongtong", "中通": "zhongtong",
    "圆通速递": "yuantong", "圆通": "yuantong", "申通快递": "shentong", "申通": "shentong",
    "韵达快递": "yunda", "韵达": "yunda", "邮政EMS": "ems", "EMS": "ems",
    "中国邮政": "youzhengguonei", "京东物流": "jd", "京东": "jd",
    "极兔速递": "jtexpress", "极兔": "jtexpress", "德邦快递": "debangkuaidi",
}
# 外部平台使用数字状态，应用内部使用稳定的业务名称；它们不等同于订单状态枚举。
STATE_MAP = {"0": "in_transit", "1": "collected", "2": "exception", "3": "signed",
             "4": "returning", "5": "out_for_delivery", "6": "returning", "8": "customs", "14": "exception"}


class TrackingProviderError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProviderResult:
    """平台适配后的统一结果；业务层无需知道原始响应字段叫什么。"""
    status: str
    provider_state: str
    events: list[dict]


def carrier_code(name, settings):
    """优先采用管理员配置的映射，再查询内置公司名；无法识别时拒绝猜测编码。"""
    code = settings.carrier_codes.get(name) or CARRIER_CODES.get(name)
    if not code or not re.fullmatch(r"[a-z0-9_]{1,60}", code):
        raise TrackingProviderError("carrier_unknown", "该快递公司尚未配置快递100编码，请联系管理员补齐映射")
    return code


def event_time(value):
    """将平台时间转为数据库使用的 UTC；无时区按北京时间解释，无法解析则保持未知。"""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or SHANGHAI).astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError, AttributeError):
        return None


def parse_result(body, code, tracking_number):
    """检查响应归属并规范化节点；不能把其他运单的数据写到当前订单中。"""
    if not isinstance(body, dict):
        raise TrackingProviderError("invalid_response", "物流平台返回的数据格式异常，已保留上次记录")
    if body.get("result") is False or body.get("returnCode") not in (None, "200", 200) or not isinstance(body.get("data"), list):
        # 不转发平台原始 message，避免其回显账号、手机号或签名。
        raw_code = str(body.get("returnCode", "unknown"))
        safe_code = raw_code if re.fullmatch(r"[A-Za-z0-9_-]{1,16}", raw_code) else "unknown"
        raise TrackingProviderError("provider_rejected", f"物流平台暂未提供有效轨迹（代码 {safe_code}），请核对账号权限、快递公司和运单")
    if str(body.get("com", "")).lower() != code or str(body.get("nu", "")).upper() != tracking_number.upper():
        raise TrackingProviderError("waybill_mismatch", "物流平台返回的快递公司或运单号不匹配，未写入轨迹")
    if len(body["data"]) > 1000:
        raise TrackingProviderError("invalid_response", "物流轨迹数量异常，未写入数据")
    events = {}
    # 用“时间、描述、位置”生成节点身份；即使平台返回重复行，也只保留一条。
    for item in body["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("context"), str) or not item["context"].strip():
            raise TrackingProviderError("invalid_response", "物流节点格式异常，已保留上次记录")
        raw_time = str(item.get("ftime") or item.get("time") or "")[:80]
        description = item["context"].strip()[:4000]
        location = str(item.get("location") or item.get("areaName") or "")[:250] or None
        occurred_at = event_time(raw_time)
        identity = [occurred_at.isoformat() if occurred_at else raw_time, description, location]
        hashed = sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        events[hashed] = {"event_hash": hashed, "occurred_at": occurred_at, "raw_time": raw_time,
                          "description": description, "location": location}
    ordered = sorted(events.values(), key=lambda item: item["occurred_at"] or datetime.min, reverse=True)
    state = str(body.get("state", ""))[:20]
    return ProviderResult(STATE_MAP.get(state, "unknown") if ordered else "unknown", state, ordered)


class Kuaidi100Provider:
    def __init__(self, settings):
        self.settings = settings

    async def query(self, code, tracking_number, phone, destination):
        """只负责平台通信与响应转换；限频、数据库事务和待办规则由 tracking.py 负责。"""
        if not self.settings.configured:
            raise TrackingProviderError("not_configured", "物流查询服务尚未配置")
        if not 6 <= len(tracking_number) <= 32:
            raise TrackingProviderError("tracking_format", "快递100查询要求运单号长度为6至32位，请核对发货记录")
        if code in {"shunfeng", "shunfengkuaiyun", "zhongtong"} and len(re.sub(r"\D", "", phone or "")) < 4:
            raise TrackingProviderError("phone_required", "该快递查询需要有效的收件电话，请核对订单地址快照")
        param = json.dumps({"com": code, "num": tracking_number, "phone": phone or "", "to": destination,
                            "show": "0", "order": "desc"}, ensure_ascii=False, separators=(",", ":"))
        # 签名针对实际提交的 param 文本计算；不能签完名后再更改 JSON 空格或字段内容。
        sign = md5((param + self.settings.key + self.settings.customer).encode()).hexdigest().upper()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), follow_redirects=False) as client:
                response = await client.post("https://poll.kuaidi100.com/poll/query.do",
                    data={"customer": self.settings.customer, "sign": sign, "param": param})
                response.raise_for_status()
                if len(response.content) > 2_000_000:
                    raise ValueError("response too large")
                body = response.json()
        except httpx.HTTPError as error:
            raise TrackingProviderError("network_error", "物流平台连接失败或超时，已保留上次成功轨迹") from error
        except ValueError as error:
            raise TrackingProviderError("invalid_response", "物流平台返回的数据无法解析，已保留上次成功轨迹") from error
        return parse_result(body, code, tracking_number)
