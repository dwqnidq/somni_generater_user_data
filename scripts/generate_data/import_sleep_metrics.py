"""轻量加载 build_sleep_structure_metrics，避免 import sleep_report 包 __init__ 拉取全量依赖。"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from typing import Callable

_PKG = "sleep_report"
_LOADED = False


def load_build_sleep_structure_metrics() -> Callable:
    """返回 sleep_score.build_sleep_structure_metrics（仅依赖 time_utils + sleep_score）。"""
    global _LOADED
    pkg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sleep_report")
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [pkg_dir]
        pkg.__package__ = _PKG
        sys.modules[_PKG] = pkg

    def _load_sub(mod_name: str):
        full = f"{_PKG}.{mod_name}"
        if full in sys.modules:
            return sys.modules[full]
        path = os.path.join(pkg_dir, f"{mod_name}.py")
        spec = importlib.util.spec_from_file_location(full, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载模块: {path}")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = _PKG
        sys.modules[full] = module
        spec.loader.exec_module(module)
        return module

    if not _LOADED:
        _load_sub("time_utils")
        _load_sub("sleep_score")
        _LOADED = True
    return sys.modules[f"{_PKG}.sleep_score"].build_sleep_structure_metrics
