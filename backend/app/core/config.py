"""[后端配置] 读取数据库连接地址；本地开发通过 backend/.env 的 DATABASE_URL 配置 MySQL。"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required; set it in backend/.env or the environment.")

# [认证功能 / Cookie 配置] 本地 HTTP 开发默认关闭 Secure；线上 HTTPS 应设置为 true。
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}

# [配送功能 / 高德服务] Web 服务 Key 仅供后端使用；安全密钥由 Nginx 代理注入 jscode。
# 新变量名与部署配置保持一致；旧名作为迁移期回退项。
AMAP_WEB_KEY = (os.getenv("AMAP_WEB_KEY") or os.getenv("AMAP_WEB_SERVICE_KEY") or "").strip()
AMAP_SERVICE_PROXY_URL = os.getenv("AMAP_SERVICE_PROXY_URL", "http://127.0.0.1:8080/_AMapService").rstrip("/")
AMAP_SHIPPING_ORIGIN_LONGITUDE = os.getenv("AMAP_SHIPPING_ORIGIN_LONGITUDE")
AMAP_SHIPPING_ORIGIN_LATITUDE = os.getenv("AMAP_SHIPPING_ORIGIN_LATITUDE")
