"""Real, sandboxed Python code execution for the standalone AI workspace.

Goals
-----
- Run user-provided Python code in an isolated subprocess.
- Enforce a hard timeout (default 5s) and bounded output size.
- Reject obvious dangerous modules (``os``, ``subprocess``, ``socket``,
  ``urllib``, ``http``, ``shutil``, ``ctypes``, ``importlib``, ``pathlib``,
  ``multiprocessing``) before exec and inside the sandboxed globals.
- Capture stdout, stderr, exit code and a structured error category.
- Never touch the host filesystem outside a tmp work dir.
- Used by ``routers/workspace_agents`` for the code agent closed loop.

The same executor is also reused to surface "real" stdout for snippet
generation (i.e. ``expected_output`` is intentionally ignored; the only
truth is what actually ran).
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Iterable, List, Optional


DEFAULT_TIMEOUT_SEC = 5.0
MAX_OUTPUT_BYTES = 8 * 1024
MAX_SOURCE_BYTES = 24 * 1024
MAX_LINES = 400

# Imports that can be used to escape the sandbox or cause destructive side
# effects on the host (the workspace runs on the dev machine).  The list is
# intentionally explicit; a real production deployment would replace it
# with a proper containerized runner.
_FORBIDDEN_MODULES = frozenset({
    "os", "sys", "subprocess", "socket", "urllib", "urllib2", "urllib3",
    "http", "httplib", "ftplib", "smtplib", "poplib", "imaplib",
    "shutil", "ctypes", "importlib", "pathlib", "multiprocessing",
    "threading", "fcntl", "pwd", "grp", "resource", "signal",
    "pickle", "shelve", "marshal", "code", "codeop", "compileall",
    "py_compile", "zipimport", "pkgutil", "asyncio", "ssl", "selectors",
    "tempfile", "glob", "fnmatch", "linecache", "pwd",
    "win32api", "win32com", "win32process", "_winreg", "msvcrt",
    "ctypes", "requests",
})

# Builtins we strip from the sandboxed namespace.  ``open`` is the
# primary one — it would let the snippet write anywhere the user can.
# We also drop dynamic exec / import / introspection that can be used
# to escape the sandbox.  ``quit`` / ``exit`` / ``help`` are *not*
# denied because they get imported transitively by the ``time`` and
# ``datetime`` modules during interpreter bootstrap; the AST scan
# already blocks the user from importing dangerous top-level modules.
_FORBIDDEN_BUILTINS = ("open", "exec", "eval", "compile", "__import__",
                        "input", "breakpoint", "globals", "locals",
                        "vars", "memoryview")


class CodeSecurityError(ValueError):
    """Raised when user code references forbidden symbols."""


@dataclass
class CodeRunResult:
    language: str
    source: str
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_ms: int
    output_truncated: bool
    source_truncated: bool
    error_category: str
    error_message: str
    line_count: int
    started_at: str
    finished_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PrivateJudgeResult:
    tests_total: int
    tests_passed: int
    exit_code: int
    timed_out: bool
    duration_ms: int
    error_category: str
    error_message: str


def _truncate(text: str, limit: int) -> tuple:
    if text is None:
        return "", False
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text, False
    encoded = encoded[:limit]
    return encoded.decode("utf-8", errors="replace") + "\n…[output truncated]", True


def _normalize_source(source: str) -> str:
    text = str(source or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def _scan_for_forbidden(source: str) -> List[str]:
    """Static AST scan to reject obvious escape hatches before exec."""
    flagged: List[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CodeSecurityError("代码包含语法错误：%s" % exc.msg) from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = (alias.name or "").split(".")[0]
                if root in _FORBIDDEN_MODULES:
                    flagged.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _FORBIDDEN_MODULES:
                flagged.append(node.module or "")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "__import__":
                flagged.append("__import__")
    return sorted(set(flagged))


def _resolve_line_count(source: str) -> int:
    return max(1, source.count("\n"))


def _build_sandbox_source(source: str) -> str:
    """Wrap user source with restricted builtins + safe stdout flush.

    The wrapper is a *real* Python entry point: it installs a restricted
    ``__builtins__`` and ``print`` before exec'ing the user code, so the
    user code can still print to stdout but cannot open files or import
    dangerous modules (both of which were blocked by the AST scan).
    """
    safe_builtins_exports = (
        "abs all any bin bool chr complex dict divmod enumerate "
        "filter float format frozenset hash hex id int isinstance "
        "issubclass iter len list map max min object oct ord pow "
        "print property range repr reversed round set slice sorted "
        "str sum tuple type zip bool classmethod delattr dict "
        "dir enumerate float format frozenset getattr hasattr "
        "issubclass iter len list map max min next object oct "
        "ord pow print property range repr reversed round set "
        "setattr slice sorted staticmethod str sum super tuple "
        "type zip __build_class__ __name__ __doc__ True False None"
    )
    safe_builtins_exports = " ".join(sorted(set(safe_builtins_exports.split())))
    denied_builtins = " ".join(_FORBIDDEN_BUILTINS)
    # safe_builtins_exports + denied_builtins are plain identifiers joined by
    # spaces; we use ``%r`` so the resulting sandbox source is a real Python
    # string literal that ``.split()`` can tokenise.
    return (
        "import builtins as _safe_builtins\n"
        "_ALLOWED = set(%r.split())\n"
        "_DENIED = set(%r.split())\n"
        "_EXPORTED = {n: getattr(_safe_builtins, n) for n in _ALLOWED "
        "if hasattr(_safe_builtins, n)}\n"
        "for _n in _DENIED:\n"
        "    _EXPORTED[_n] = lambda *_a, **_k: (_ for _ in ()).throw("
        "PermissionError('内置 ' + _n + ' 被禁用，请用其他方式实现'))\n"
        "_EXPORTED['__builtins__'] = _EXPORTED\n"
        "import sys as _safe_sys\n"
        "class _SafeStream:\n"
        "    def __init__(self, real):\n"
        "        self._real = real\n"
        "    def write(self, s):\n"
        "        return self._real.write(s)\n"
        "    def flush(self):\n"
        "        return self._real.flush()\n"
        "    def isatty(self):\n"
        "        return False\n"
        "_safe_sys.stdout = _SafeStream(_safe_sys.stdout)\n"
        "_safe_sys.stderr = _SafeStream(_safe_sys.stderr)\n"
        "_USER_SOURCE = %r\n"
        "exec(compile(_USER_SOURCE, '<workspace_code>', 'exec'), _EXPORTED, None)\n"
    ) % (
        safe_builtins_exports,
        denied_builtins,
        _normalize_source(source),
    )


def _launch_subprocess(source: str, timeout_sec: float) -> tuple:
    fd, path = tempfile.mkstemp(prefix="workspace_code_", suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(source)
        creationflags = 0
        if os.name == "nt":
            # Keep the subprocess in its own group so it cannot open a
            # console window.  CREATE_NO_WINDOW only exists on Windows.
            creationflags = 0x08000000
        # Inherit the parent environment so the Python interpreter can
        # initialise the hash randomisation seed; only the variables that
        # would let the user escape the sandbox are stripped.
        sandbox_env = {
            key: value for key, value in os.environ.items()
            if key not in {
                "PYTHONSTARTUP", "PYTHONPATH", "PYTHONHOME",
                "PYTHONCASEOK", "PYTHONIOENCODING",
            }
        }
        sandbox_env["PYTHONDONTWRITEBYTECODE"] = "1"
        sandbox_env["PYTHONHASHSEED"] = "0"
        sandbox_env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [sys.executable, "-I", "-S", "-X", "utf8", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=tempfile.gettempdir(),
            env=sandbox_env,
            creationflags=creationflags,
        )
        return proc, path
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def run_python(source: str, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> CodeRunResult:
    """Run Python code in a sandboxed subprocess and return a structured result."""
    started = time.time()
    finished_at_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
    normalized = _normalize_source(source)
    source_truncated = False
    if len(normalized.encode("utf-8")) > MAX_SOURCE_BYTES:
        normalized = normalized.encode("utf-8")[:MAX_SOURCE_BYTES].decode("utf-8", errors="replace")
        normalized += "\n…[source truncated]"
        source_truncated = True
    line_count = _resolve_line_count(normalized)
    if line_count > MAX_LINES:
        return CodeRunResult(
            language="python",
            source=normalized,
            stdout="",
            stderr="代码超过 %d 行，沙箱拒绝执行。" % MAX_LINES,
            exit_code=1,
            timed_out=False,
            duration_ms=0,
            output_truncated=False,
            source_truncated=source_truncated,
            error_category="source_too_long",
            error_message="代码行数超过沙箱限制",
            line_count=line_count,
            started_at=finished_at_iso,
            finished_at=finished_at_iso,
        )
    try:
        forbidden = _scan_for_forbidden(normalized)
    except CodeSecurityError as exc:
        return CodeRunResult(
            language="python",
            source=normalized,
            stdout="",
            stderr=str(exc),
            exit_code=1,
            timed_out=False,
            duration_ms=0,
            output_truncated=False,
            source_truncated=source_truncated,
            error_category="syntax_error",
            error_message=str(exc),
            line_count=line_count,
            started_at=finished_at_iso,
            finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
    if forbidden:
        message = "代码引用了被沙箱禁用的模块：%s。请改用其他实现方式。" % "、".join(forbidden)
        return CodeRunResult(
            language="python",
            source=normalized,
            stdout="",
            stderr=message,
            exit_code=1,
            timed_out=False,
            duration_ms=0,
            output_truncated=False,
            source_truncated=source_truncated,
            error_category="forbidden_module",
            error_message=message,
            line_count=line_count,
            started_at=finished_at_iso,
            finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    sandbox_source = _build_sandbox_source(normalized)
    proc = None
    script_path = None
    try:
        proc, script_path = _launch_subprocess(sandbox_source, timeout_sec)
    except Exception as exc:
        return CodeRunResult(
            language="python",
            source=normalized,
            stdout="",
            stderr="无法启动 Python 子进程：%s" % exc,
            exit_code=1,
            timed_out=False,
            duration_ms=0,
            output_truncated=False,
            source_truncated=source_truncated,
            error_category="spawn_failed",
            error_message=str(exc),
            line_count=line_count,
            started_at=finished_at_iso,
            finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=1.0)
        except Exception:
            stdout_bytes, stderr_bytes = b"", b""
    exit_code = proc.returncode
    duration_ms = int((time.time() - started) * 1000)
    stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
    stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")
    stdout, stdout_truncated = _truncate(stdout, MAX_OUTPUT_BYTES)
    stderr, stderr_truncated = _truncate(stderr, MAX_OUTPUT_BYTES)
    output_truncated = stdout_truncated or stderr_truncated
    if timed_out:
        error_category = "timeout"
        error_message = "代码执行超过 %.1fs 沙箱超时，已被强制终止。" % timeout_sec
    elif exit_code != 0:
        error_category = "runtime_error"
        first_line = (stderr.strip().splitlines() or [""])[0]
        error_message = first_line[:240] or "执行返回非零退出码"
    else:
        error_category = "ok"
        error_message = ""
    return CodeRunResult(
        language="python",
        source=normalized,
        stdout=stdout,
        stderr=stderr,
        exit_code=int(exit_code or 0),
        timed_out=timed_out,
        duration_ms=duration_ms,
        output_truncated=output_truncated,
        source_truncated=source_truncated,
        error_category=error_category,
        error_message=error_message,
        line_count=line_count,
        started_at=finished_at_iso,
        finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def _private_judge_source_allowed(source: str) -> tuple[bool, str]:
    """Fail closed on introspection that could cross the private judge boundary."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, "syntax_error:%s" % exc.msg
    forbidden = _scan_for_forbidden(source)
    if forbidden:
        return False, "forbidden_module:%s" % ",".join(forbidden)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return False, "imports_not_supported_in_submission"
        if isinstance(node, ast.Attribute):
            return False, "attribute_access_not_supported_in_submission"
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            return False, "private_names_not_supported"
        if isinstance(node, ast.ClassDef):
            return False, "classes_not_supported_in_submission"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_"):
            return False, "private_names_not_supported"
        if isinstance(node, ast.arg) and node.arg.startswith("_"):
            return False, "private_names_not_supported"
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and (
            node.value.startswith("_") or "__" in node.value
        ):
            return False, "private_string_names_not_supported"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
            "getattr", "setattr", "delattr", "dir", "type", "object", "vars", "globals", "locals", "eval", "exec", "compile", "__import__",
        }:
            return False, "reflection_not_supported_in_submission"
    return True, ""


def _build_closure_counter_judge_source(source: str, nonce: str) -> str:
    """Build a judge wrapper whose authority namespace is never learner-owned.

    Learner source is executed in a distinct globals dictionary with captured
    stdout/stderr. Hidden inputs and trusted primitives live only in the outer
    wrapper. The only parent-visible output is a nonce-bound result record.
    """
    safe_exports = (
        "abs all any bool dict enumerate filter float int isinstance iter len "
        "list map max min next print range reversed round set slice sorted str "
        "sum tuple zip True False None Exception RuntimeError ValueError TypeError"
    )
    safe_exports = " ".join(sorted(set(safe_exports.split())))
    denied = " ".join(_FORBIDDEN_BUILTINS)
    return (
        "import builtins as _trusted_builtins\n"
        "import io as _trusted_io\n"
        "import sys as _trusted_sys\n"
        "_allowed = set(%r.split())\n"
        "_denied = set(%r.split())\n"
        "_learner_builtins = {n: getattr(_trusted_builtins, n) for n in _allowed if hasattr(_trusted_builtins, n)}\n"
        "for _name in _denied:\n"
        "    _learner_builtins[_name] = lambda *_a, **_k: (_ for _ in ()).throw(PermissionError('operation disabled'))\n"
        "_learner_globals = {'__builtins__': _learner_builtins, '__name__': '__learner__'}\n"
        "_real_stdout, _real_stderr = _trusted_sys.stdout, _trusted_sys.stderr\n"
        "_trusted_sys.stdout, _trusted_sys.stderr = _trusted_io.StringIO(), _trusted_io.StringIO()\n"
        "_bits = []\n"
        "_error = ''\n"
        "try:\n"
        "    exec(compile(%r, '<code_lab_submission>', 'exec'), _learner_globals, None)\n"
        "    _make = _learner_globals.get('make_counter')\n"
        "    if not _trusted_builtins.callable(_make):\n"
        "        raise TypeError('make_counter must be callable')\n"
        "    _first = _make(2)\n"
        "    _v11, _v12 = _first(), _first(2)\n"
        "    _bits.append(_trusted_builtins.type(_v11) is _trusted_builtins.int and _v11 == 3 and _trusted_builtins.type(_v12) is _trusted_builtins.int and _v12 == 5)\n"
        "    _left, _right = _make(0), _make(10)\n"
        "    _v21, _v22, _v23 = _left(), _right(), _left()\n"
        "    _bits.append(_trusted_builtins.type(_v21) is _trusted_builtins.int and _v21 == 1 and _trusted_builtins.type(_v22) is _trusted_builtins.int and _v22 == 11 and _trusted_builtins.type(_v23) is _trusted_builtins.int and _v23 == 2)\n"
        "    _transfer = _make(5)\n"
        "    _v31, _v32 = _transfer(3), _transfer(-2)\n"
        "    _bits.append(_trusted_builtins.type(_v31) is _trusted_builtins.int and _v31 == 8 and _trusted_builtins.type(_v32) is _trusted_builtins.int and _v32 == 6)\n"
        "except _trusted_builtins.BaseException as _exc:\n"
        "    _error = _trusted_builtins.type(_exc).__name__\n"
        "finally:\n"
        "    _trusted_sys.stdout, _trusted_sys.stderr = _real_stdout, _real_stderr\n"
        "_passed = _trusted_builtins.sum(1 for _value in _bits if _value is True)\n"
        "_real_stdout.write(%r + '|' + _trusted_builtins.str(_passed) + '|3|' + _error + '\\n')\n"
        "_real_stdout.flush()\n"
    ) % (safe_exports, denied, _normalize_source(source), nonce)


def run_private_closure_counter_judge(source: str, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> PrivateJudgeResult:
    """Use the existing bounded subprocess runner in private-judge mode."""
    started = time.time()
    normalized = _normalize_source(source)
    if len(normalized.encode("utf-8")) > MAX_SOURCE_BYTES or _resolve_line_count(normalized) > MAX_LINES:
        return PrivateJudgeResult(3, 0, 1, False, 0, "source_too_long", "submission source exceeds limits")
    try:
        allowed, reason = _private_judge_source_allowed(normalized)
    except CodeSecurityError as exc:
        allowed, reason = False, str(exc)
    if not allowed:
        category = reason.split(":", 1)[0] or "unsupported_submission"
        return PrivateJudgeResult(3, 0, 1, False, 0, category, reason)
    nonce = uuid.uuid4().hex
    proc = None
    path = None
    try:
        proc, path = _launch_subprocess(_build_closure_counter_judge_source(normalized, nonce), timeout_sec)
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout_sec)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            stdout_bytes, stderr_bytes = proc.communicate(timeout=1.0)
    except Exception as exc:
        return PrivateJudgeResult(3, 0, 1, False, int((time.time() - started) * 1000), "spawn_failed", str(exc))
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    duration_ms = int((time.time() - started) * 1000)
    if timed_out:
        return PrivateJudgeResult(3, 0, int(proc.returncode or 1), True, duration_ms, "timeout", "submission judge timed out")
    stdout = (stdout_bytes or b"").decode("utf-8", errors="replace").strip().splitlines()
    exact = [line for line in stdout if line.startswith(nonce + "|")]
    if int(proc.returncode or 0) != 0 or len(exact) != 1:
        return PrivateJudgeResult(3, 0, int(proc.returncode or 1), False, duration_ms, "judge_protocol_error", "private judge result unavailable")
    parts = exact[0].split("|", 3)
    try:
        passed, total = int(parts[1]), int(parts[2])
    except (IndexError, ValueError):
        return PrivateJudgeResult(3, 0, 1, False, duration_ms, "judge_protocol_error", "private judge result invalid")
    if total != 3 or not 0 <= passed <= total:
        return PrivateJudgeResult(3, 0, 1, False, duration_ms, "judge_protocol_error", "private judge result invalid")
    runtime_name = parts[3] if len(parts) > 3 else ""
    return PrivateJudgeResult(total, passed, 0 if not runtime_name else 1, False, duration_ms, "ok" if not runtime_name else "runtime_error", runtime_name)


def explain_python_lines(source: str) -> List[dict]:
    """Return a list of {lineno, code, hint} for line-by-line explanation.

    The hints are static knowledge-base style fallbacks.  The full
    natural-language explanation is produced by the model (see
    ``workspace_agents``); this list is what the UI binds to the
    "逐行解释" interaction.
    """
    normalized = _normalize_source(source)
    lines = normalized.splitlines()
    out = []
    for index, line in enumerate(lines, start=1):
        hint = _hint_for_line(line)
        out.append({"lineno": index, "code": line, "hint": hint})
    return out


_RANGE_PATTERN = re.compile(r"range\(\s*([^\)]*)\)")
_FOR_VARIABLE_PATTERN = re.compile(r"^\s*for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(.+?)\s*:\s*$")
_WHILE_PATTERN = re.compile(r"^\s*while\s+(.+?)\s*:\s*$")
_DEF_PATTERN = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*:\s*$")
_PRINT_PATTERN = re.compile(r"^\s*print\s*\(")


def _hint_for_line(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ""
    m = _DEF_PATTERN.match(line)
    if m:
        return "定义函数 %s(%s)，函数体需要缩进。" % (m.group(1), m.group(2) or "")
    m = _FOR_VARIABLE_PATTERN.match(line)
    if m:
        var, iterable = m.group(1), m.group(2)
        range_args = _RANGE_PATTERN.findall(iterable)
        if range_args:
            return "for 循环：把 %s 依次取自 %s，每次执行循环体。" % (var, iterable)
        return "for 循环：把 %s 依次取自 %s，每次执行循环体。" % (var, iterable)
    m = _WHILE_PATTERN.match(line)
    if m:
        return "while 循环：条件 %s 为 True 时反复执行循环体。" % m.group(1)
    if _PRINT_PATTERN.match(line):
        return "print()：把结果写到标准输出，独立工作区会捕获到这次输出。"
    if stripped.startswith("return "):
        return "return：把表达式的值作为函数返回值。"
    if stripped.startswith("import ") or stripped.startswith("from "):
        return "导入语句：加载外部模块；沙箱已禁用破坏性模块。"
    if "=" in stripped and not stripped.startswith("==") and "==" not in stripped.split("=", 1)[0]:
        return "赋值语句：把右边的值绑定到左边的变量。"
    return ""


def extract_knowledge_points(source: str, topic_hint: str = "") -> List[str]:
    """Best-effort list of knowledge points covered by the snippet.

    The list is fed to the exam agent as ``source_artifact_topic`` so the
    generated quiz is anchored to the actual code (not the user's chat
    history).  This is intentionally a small, deterministic set so the
    exam agent can extend it via the LLM prompt.
    """
    points: List[str] = []
    text = str(source or "")
    topic = str(topic_hint or "").strip()
    if _RANGE_PATTERN.search(text):
        points.append("range 函数的参数语义（含 start / stop / step）")
    if re.search(r"for\s+\w+\s+in", text):
        points.append("for 循环遍历可迭代对象")
    if re.search(r"while\s+", text):
        points.append("while 循环条件判断与终止")
    if re.search(r"def\s+\w+\s*\(", text):
        points.append("函数定义与参数传递")
    if re.search(r"return\s+", text):
        points.append("函数返回值")
    if re.search(r"if\s+.+:", text):
        points.append("if 条件分支")
    if re.search(r"\.append\(|\.extend\(|\[\s*\w+\s*\]", text):
        points.append("列表与序列操作")
    if re.search(r"\{.*:.*\}", text):
        points.append("字典字面量与键值对")
    if re.search(r"try\s*:", text):
        points.append("异常处理 try/except")
    if topic:
        points.insert(0, "主题：%s" % topic)
    return points
