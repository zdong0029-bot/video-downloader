"""账号与会话。

密码用 PBKDF2-HMAC-SHA256 加随机盐存储，不保存明文，也不可逆推。
只用标准库，不引入额外依赖。

注意：局域网走的是 HTTP 不是 HTTPS，登录时密码在局域网内是明文传输的。
家里用没问题，但不要把这个端口暴露到公网。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)
USERS_FILE = DATA / "users.json"
SESSIONS_FILE = DATA / "sessions.json"

ITERATIONS = 200_000
SESSION_DAYS = 30


# ----------------------------------------------------------------- 密码

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, want = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)
        )
    except (ValueError, TypeError):
        return False
    # 固定时间比较，避免按字符逐位试探
    return secrets.compare_digest(dk.hex(), want)


# ----------------------------------------------------------------- 存储

def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_users() -> dict[str, dict]:
    return _read(USERS_FILE, {})


def save_users(users: dict[str, dict]) -> None:
    _write(USERS_FILE, users)


def load_sessions() -> dict[str, dict]:
    sessions = _read(SESSIONS_FILE, {})
    now = time.time()
    alive = {k: v for k, v in sessions.items() if v.get("expires", 0) > now}
    if len(alive) != len(sessions):
        _write(SESSIONS_FILE, alive)
    return alive


def save_sessions(sessions: dict[str, dict]) -> None:
    _write(SESSIONS_FILE, sessions)


# ----------------------------------------------------------------- 账号

@dataclass
class User:
    name: str
    is_admin: bool


def has_users() -> bool:
    return bool(load_users())


def create_user(name: str, password: str, is_admin: bool = False) -> None:
    name = name.strip()
    if not name:
        raise ValueError("用户名不能为空")
    if len(name) > 32:
        raise ValueError("用户名太长")
    if len(password) < 4:
        raise ValueError("密码至少 4 位")

    users = load_users()
    if name in users:
        raise ValueError(f"用户名「{name}」已存在")

    users[name] = {
        "password": hash_password(password),
        "is_admin": bool(is_admin),
        "created": time.time(),
    }
    save_users(users)


def set_password(name: str, password: str) -> None:
    if len(password) < 4:
        raise ValueError("密码至少 4 位")
    users = load_users()
    if name not in users:
        raise ValueError("用户不存在")
    users[name]["password"] = hash_password(password)
    save_users(users)
    # 改密后把该用户的登录状态全部作废，逼其重新登录
    sessions = {k: v for k, v in load_sessions().items() if v.get("user") != name}
    save_sessions(sessions)


def delete_user(name: str) -> None:
    users = load_users()
    if name not in users:
        raise ValueError("用户不存在")
    if users[name].get("is_admin"):
        admins = [n for n, u in users.items() if u.get("is_admin")]
        if len(admins) <= 1:
            raise ValueError("不能删除唯一的管理员")
    del users[name]
    save_users(users)
    sessions = {k: v for k, v in load_sessions().items() if v.get("user") != name}
    save_sessions(sessions)


def authenticate(name: str, password: str) -> User | None:
    users = load_users()
    u = users.get(name.strip())
    if not u or not verify_password(password, u["password"]):
        return None
    return User(name=name.strip(), is_admin=bool(u.get("is_admin")))


# ----------------------------------------------------------------- 会话

def start_session(name: str) -> str:
    token = secrets.token_urlsafe(32)
    sessions = load_sessions()
    sessions[token] = {
        "user": name,
        "expires": time.time() + SESSION_DAYS * 86400,
    }
    save_sessions(sessions)
    return token


def end_session(token: str) -> None:
    sessions = load_sessions()
    if sessions.pop(token, None) is not None:
        save_sessions(sessions)


def user_of_token(token: str | None) -> User | None:
    if not token:
        return None
    s = load_sessions().get(token)
    if not s:
        return None
    users = load_users()
    u = users.get(s["user"])
    if not u:
        return None
    return User(name=s["user"], is_admin=bool(u.get("is_admin")))
