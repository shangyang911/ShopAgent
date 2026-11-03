"""认证功能 / 密码与会话安全：使用标准库完成密码哈希和随机会话令牌。"""
import hashlib
import hmac
import secrets

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    """为密码生成带随机盐的 scrypt 哈希，数据库不保存明文密码。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    """验证登录密码，并用恒定时间比较避免泄露摘要差异。"""
    try:
        algorithm, n_value, salt_hex, digest_hex = encoded_hash.split("$", maxsplit=3)
        if algorithm != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_value),
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_session_token() -> str:
    """生成不可预测的浏览器会话令牌。"""
    return secrets.token_urlsafe(48)


def hash_session_token(token: str) -> str:
    """数据库只存储会话令牌 SHA-256 摘要。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
