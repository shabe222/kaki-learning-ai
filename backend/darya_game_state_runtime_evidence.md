# DARYA Game-State Runtime Evidence

Date: 2026-09-04

## Environment

- Interpreter: Python 3.13.0
- Disposable venv: `C:\Users\wuzbo\AppData\Local\Temp\darya-game-state-py313-20260904`
- Project `.venv311` / `.venv`: not used or modified.

## Commands and results

From `C:\Users\wuzbo\Desktop\柿子智学-DISCOVERY-20260824\KAKI-WINDOWS-FINAL\backend`:

```powershell
& 'C:\Users\wuzbo\AppData\Local\Temp\darya-game-state-py313-20260904\Scripts\python.exe' -m pip install fastapi uvicorn sqlalchemy pydantic pydantic-settings pytest pytest-asyncio httpx python-multipart aiosqlite python-jose bcrypt python-dotenv
```

Result: PASS (`Successfully installed ...`). The disposable venv additionally required `redis` because the existing `database` package imports it during test collection:

```powershell
& 'C:\Users\wuzbo\AppData\Local\Temp\darya-game-state-py313-20260904\Scripts\python.exe' -m pip install redis
```

Result: PASS (`Successfully installed redis-8.1.0`). No project dependency manifest or accepted environment was changed.

```powershell
& 'C:\Users\wuzbo\AppData\Local\Temp\darya-game-state-py313-20260904\Scripts\python.exe' -c "import fastapi, uvicorn, sqlalchemy, pydantic, pydantic_settings, pytest, pytest_asyncio, httpx, multipart, aiosqlite, jose, bcrypt, dotenv, redis; print('dependency imports: PASS')"
```

Result: `dependency imports: PASS`

```powershell
& 'C:\Users\wuzbo\AppData\Local\Temp\darya-game-state-py313-20260904\Scripts\python.exe' -m pytest -q tests/test_darya_game_state.py
```

Result:

```text
....                                                                     [100%]
4 passed in 1.39s
```

The focused suite now includes a real async FastAPI/HTTP contract test for `GET` and `PUT /api/darya-game/state`: successful initialization/update, stale revision rejection with HTTP 409 and `STALE_GAME_STATE_REVISION`, oversized payload rejection with HTTP 413, and persistence unchanged after both rejected writes.
