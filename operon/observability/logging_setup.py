"""统一 logging 配置。

背景: 项目原本全仓只有 `logging.getLogger(__name__)` 调用 (15+ 处),
但完全没有 `basicConfig / dictConfig / addHandler` 配置。
Python logging 的默认行为是 WARNING 级别 + stderr, 所以所有 logger.info / debug
默认根本不输出, 浪费了埋点。

本模块在 API 启动时调用一次 setup_logging(), 配置:
- RotatingFileHandler → ~/.axiom/logs/operon.log (10MB × 5 份)
- StreamHandler → stderr (保持原有行为)

幂等: 重复调用不会叠加 handler。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_DEFAULT_BACKUP_COUNT = 5
_CONFIGURED_FLAG = "axiom_logging_configured"


def setup_logging(
    level: str | int = "INFO",
    log_dir: Path | str | None = None,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backup_count: int = _DEFAULT_BACKUP_COUNT,
) -> None:
    """配置 root logger: stderr + rotating file。

    幂等: 已经配置过则只调整 level。
    log_dir 为 None 时默认 ~/.axiom/logs/。
    """
    root = logging.getLogger()

    # 幂等: 已配置过只调 level
    if getattr(root, _CONFIGURED_FLAG, False):
        root.setLevel(level)
        return

    root.setLevel(level)
    formatter = logging.Formatter(_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT)

    # stderr handler (保持原有行为)
    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)

    # file handler (新增: 持久化日志, 便于事后追溯)
    if log_dir is not None:
        log_path = Path(log_dir)
        try:
            log_path.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path / "operon.log",
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:
            # 日志目录不可写等情况下退化为仅 stderr (不抛异常, 不影响主流程)
            pass

    setattr(root, _CONFIGURED_FLAG, True)
