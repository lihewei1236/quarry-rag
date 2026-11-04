"""基于 loguru 的统一日志。"""
import sys
from pathlib import Path

from loguru import logger

from quarry.infra.settings import get_settings

_CONFIGURED = False


def setup_logging() -> None:
    """初始化全局日志。多次调用只生效一次。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    s = get_settings()
    logger.remove()

    # 控制台：彩色、带模块位置
    logger.add(
        sys.stdout,
        level=s.log_level,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <7}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        backtrace=True,
        diagnose=(s.app_env == "development"),
        colorize=True,
    )

    # 文件：10 MB 轮转，保留 7 天，压缩成 zip
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(
        log_dir / "quarry_{time:YYYY-MM-DD}.log",
        level=s.log_level,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        diagnose=False,
    )

    _CONFIGURED = True


def get_logger():
    """业务代码的统一入口。"""
    setup_logging()
    return logger