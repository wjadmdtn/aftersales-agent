"""保证仓库里不出现真实密钥。

这个测试会扫全仓库的文本文件。任何人误把 key 写进代码、文档或用例，
CI 会立刻失败。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# 常见密钥格式
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                    # OpenAI / DashScope
    re.compile(r"sk-[a-f0-9]{32,}"),                        # 部分厂商
    re.compile(r"[A-Za-z0-9]{32}\.[A-Za-z0-9]{32}"),        # 双段 token
    re.compile(r"ghp_[A-Za-z0-9]{36}"),                     # GitHub PAT
    re.compile(r"AKIA[0-9A-Z]{16}"),                        # AWS
]

# 允许出现的占位符
ALLOWED_PLACEHOLDERS = {
    "your_api_key_here",
    "***",
    "<从环境变量注入>",
    "test-key",
    "fake-key",
}

SCAN_SUFFIXES = {".py", ".md", ".json", ".jsonl", ".toml", ".yaml", ".yml", ".example"}

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "_prototype",
    ".pytest_cache",
}


def _iter_files():
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in SCAN_SUFFIXES:
            continue
        yield path


@pytest.mark.parametrize("pattern", SECRET_PATTERNS, ids=lambda p: p.pattern[:20])
def test_no_secret_pattern(pattern: re.Pattern):
    hits: list[str] = []
    for path in _iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in pattern.finditer(text):
            if match.group(0) in ALLOWED_PLACEHOLDERS:
                continue
            hits.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0)[:12]}...")
    assert not hits, "仓库中出现疑似密钥：\n" + "\n".join(hits)


def test_env_file_not_tracked():
    """.env 必须被 gitignore 排除。"""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert ".env.local" in gitignore


def test_env_example_has_no_real_values():
    """.env.example 只能是占位符。"""
    example = REPO_ROOT / "backend" / ".env.example"
    if not example.exists():
        pytest.skip("no .env.example")
    text = example.read_text(encoding="utf-8")
    for pattern in SECRET_PATTERNS:
        assert not pattern.search(text), "示例配置中出现疑似密钥"
