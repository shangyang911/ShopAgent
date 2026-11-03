"""快递100 V2 电子面单。只获取图片面单，不直接触发云打印或交接出库。"""
import json
import os
import re
import time
from dataclasses import dataclass, field
from hashlib import md5
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException


def safe_label_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    # 官方返回面单短链；仅允许该平台，拒绝可执行协议及用户提供的任意站点。
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "api.kuaidi100.com" or parsed.username or parsed.password or port not in (None, 80, 443) or not parsed.path.startswith("/label/"):
        return None
    return parsed._replace(scheme="https").geturl()


@dataclass(frozen=True)
class LabelSettings:
    key: str = field(default="", repr=False)
    secret: str = field(default="", repr=False)
    sender: dict = field(default_factory=dict, repr=False)
    accounts: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls):
        def obj(name):
            try:
                value = json.loads(os.getenv(name, "{}"))
                return value if isinstance(value, dict) else {}
            except ValueError:
                return {}
        return cls(os.getenv("KD100_LABEL_KEY", "").strip(), os.getenv("KD100_LABEL_SECRET", "").strip(),
                   obj("KD100_LABEL_SENDER"), obj("KD100_LABEL_ACCOUNTS"))

    @property
    def configured(self):
        return bool(self.key and self.secret and all(self.sender.get(k) for k in ("name", "mobile", "printAddr")) and self.accounts)

    def account(self, code):
        account = self.accounts.get(code)
        if not self.configured or not isinstance(account, dict) or not account.get("partnerId") or not account.get("tempId"):
            raise HTTPException(503, "电子面单尚未配置该快递的授权、月结账号、模板或寄件地址")
        allowed = {"partnerId", "partnerKey", "partnerSecret", "partnerName", "net", "code", "checkMan", "tbNet", "tempId", "payType", "expType"}
        return {key: value for key, value in account.items() if key in allowed}


class LabelProviderError(Exception):
    def __init__(self, message, *, uncertain=True):
        super().__init__(message)
        self.uncertain = uncertain


class LabelProvider:
    def __init__(self, settings):
        self.settings = settings

    async def create(self, code, order, weight, reference):
        params = {**self.settings.account(code), "kuaidicom": code,
                  "recMan": {"name": order.recipient_name, "mobile": order.recipient_phone,
                             "printAddr": f"{order.province}{order.city}{order.district}{order.address_detail}"},
                  "sendMan": self.settings.sender, "cargo": "商品", "count": 1, "needChild": "0",
                  "weight": float(weight), "printType": "IMAGE", "orderId": reference, "reorder": False}
        param = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        timestamp = str(int(time.time() * 1000))
        sign = md5((param + timestamp + self.settings.key + self.settings.secret).encode()).hexdigest().upper()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), follow_redirects=False) as client:
                response = await client.post("https://api.kuaidi100.com/label/order", data={
                    "method": "order", "key": self.settings.key, "t": timestamp, "sign": sign, "param": param})
                response.raise_for_status()
                if len(response.content) > 1_000_000:
                    raise ValueError("oversize")
                body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise LabelProviderError("面单请求结果待核实；请在快递100后台核对，避免重复取号") from error
        if not isinstance(body, dict):
            raise LabelProviderError("面单响应异常，结果待核实")
        data = body.get("data")
        # 30011 是 orderId + reorder=false 的原结果重放。
        if str(body.get("code")) not in {"200", "30011"}:
            definitive = str(body.get("code")) in {"30001", "30002", "30003", "30004", "30006", "30007"}
            raise LabelProviderError("面单申请未成功，请核对平台账号和模板；不确定结果须先在平台核实", uncertain=not definitive)
        if not isinstance(data, dict):
            raise LabelProviderError("平台未返回有效面单，结果待核实")
        number, task_id = data.get("kuaidinum"), data.get("taskId")
        url = safe_label_url(data.get("label"))
        if not isinstance(number, str) or not re.fullmatch(r"[A-Za-z0-9-]{6,80}", number) or not isinstance(task_id, str) or not 1 <= len(task_id) <= 100 or not url:
            raise LabelProviderError("平台未返回完整单号及面单链接，请在平台核实取号结果")
        return {"tracking_number": number, "provider_task_id": task_id, "label_url": url}
