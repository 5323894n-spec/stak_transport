# -*- coding: utf-8 -*-
"""Запуск системы.

    python run.py           — обычный запуск, http://127.0.0.1:8000
    python run.py --demo    — при первом запуске наполнит базу демо-данными
    python run.py --port 8080 — другой порт
"""
import sys
import uvicorn

if __name__ == "__main__":
    port = 8000
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if "--demo" in sys.argv:
        from app import seed
        seed.run()
    print(f"АТП-система: http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=False)
