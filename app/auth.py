# -*- coding: utf-8 -*-
"""Авторизация: пользователи, сессии, роли."""
import hashlib, os, binascii, secrets, datetime
from fastapi import Request, HTTPException
from . import db

ROLES = ["админ", "диспетчер", "эксплуатация", "кадры", "бухгалтер",
         "механик", "медик", "топливо", "руководитель", "водитель",
         "мастер ремонта", "слесарь", "механик контроля", "склад"]

# право на запись по разделам (упрощённая матрица; админ может всё)
WRITE_ACCESS = {
    "диспетчер": {"orders", "waybills", "roster", "summary"},
    "эксплуатация": {"routes", "trips", "roster", "orders", "summary"},
    "кадры": {"drivers", "absences", "roster"},
    "бухгалтер": {"export1c", "timesheet"},
    "механик": {"tech", "buses"},
    "медик": {"medical"},
    "топливо": {"fuel", "buses"},
    "мастер ремонта": {"repairs", "repair_orders"},
    "слесарь": {"repair_work"},
    "механик контроля": {"repair_inspections"},
    "склад": {"repair_stock"},
}

def hash_password(password: str, salt: bytes = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120000)
    return binascii.hexlify(salt).decode() + "$" + binascii.hexlify(dk).decode()

def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$")
        salt = binascii.unhexlify(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120000)
        return binascii.hexlify(dk).decode() == dk_hex
    except Exception:
        return False

def login(con, username, password, ip=""):
    u = con.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    if not u or not verify_password(password, u["password_hash"]):
        db.audit(con, username, "неудачный вход", "session", None, ip=ip)
        con.commit()
        return None
    token = secrets.token_hex(32)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT INTO sessions(token,user_id,created,last_seen) VALUES(?,?,?,?)", (token, u["id"], now, now))
    db.audit(con, username, "вход в систему", "session", None, ip=ip)
    con.commit()
    return {"token": token, "username": u["username"], "full_name": u["full_name"], "role": u["role"]}

def current_user(request: Request):
    """FastAPI dependency: возвращает пользователя по токену."""
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else request.query_params.get("token", "")
    if not token:
        raise HTTPException(401, "Не авторизован")
    con = db.connect()
    try:
        row = con.execute(
            "SELECT u.*, s.last_seen, s.token FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (token,)).fetchone()
        if not row or not row["active"]:
            raise HTTPException(401, "Сессия недействительна")
        st = db.get_settings(con)
        timeout = int(st.get("session_timeout_min", "120"))
        last = datetime.datetime.fromisoformat(row["last_seen"])
        if datetime.datetime.now() - last > datetime.timedelta(minutes=timeout):
            con.execute("DELETE FROM sessions WHERE token=?", (token,))
            con.commit()
            raise HTTPException(401, "Сессия истекла из-за бездействия")
        con.execute("UPDATE sessions SET last_seen=? WHERE token=?",
                    (datetime.datetime.now().isoformat(timespec="seconds"), token))
        con.commit()
        return {"id": row["id"], "username": row["username"], "full_name": row["full_name"], "role": row["role"]}
    finally:
        con.close()

def require_write(user, section):
    if user["role"] == "админ":
        return
    if section not in WRITE_ACCESS.get(user["role"], set()):
        raise HTTPException(403, f"Роль «{user['role']}» не имеет права изменять раздел «{section}»")

def ensure_admin(con):
    if not con.execute("SELECT 1 FROM users").fetchone():
        con.execute("INSERT INTO users(username,password_hash,full_name,role) VALUES(?,?,?,?)",
                    ("admin", hash_password("admin"), "Администратор системы", "админ"))
        con.commit()
