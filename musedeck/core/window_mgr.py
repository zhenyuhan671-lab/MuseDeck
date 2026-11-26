"""Windows 窗口管理：优雅关闭、多窗口平铺排版、显隐切换与 Profile 磁盘体积统计。"""

from __future__ import annotations

import ctypes
import math
import sys
import time
from ctypes import wintypes
from pathlib import Path


def compute_tile_rects(
    count: int,
    work_area: tuple[int, int, int, int],
    layout: str = "grid",
    gap: int = 8,
) -> list[tuple[int, int, int, int]]:
    """纯数学计算多窗口平铺矩形 [(x, y, w, h), ...]，保证无重叠且在工作区内。"""
    if count <= 0:
        return []
    left, top, right, bottom = work_area
    total_w = max(100, right - left)
    total_h = max(100, bottom - top)

    if layout == "side_by_side":
        cols = count
        rows = 1
    else:
        if count == 1:
            cols, rows = 1, 1
        elif count == 2:
            cols, rows = 2, 1
        else:
            cols = math.ceil(math.sqrt(count))
            rows = math.ceil(count / cols)

    cell_w = max(200, (total_w - gap * (cols + 1)) // cols)
    cell_h = max(180, (total_h - gap * (rows + 1)) // rows)

    rects: list[tuple[int, int, int, int]] = []
    for idx in range(count):
        r, c = divmod(idx, cols)
        x = left + gap + c * (cell_w + gap)
        y = top + gap + r * (cell_h + gap)
        rects.append((x, y, cell_w, cell_h))
    return rects


def get_work_area() -> tuple[int, int, int, int]:
    """获取 Windows 主屏可用工作区 (left, top, right, bottom)。"""
    if not sys.platform.startswith("win"):
        return (0, 0, 1920, 1040)
    try:
        rect = wintypes.RECT()
        SPI_GETWORKAREA = 0x0030
        ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return (0, 0, 1920, 1040)


def find_visible_windows_for_pid(pid: int) -> list[int]:
    """查找目标进程所有可见顶层窗口句柄。"""
    if not sys.platform.startswith("win"):
        return []
    try:
        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            proc_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value == pid and user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    found.append(int(hwnd))
            return True

        user32.EnumWindows(_enum, 0)
        return found
    except Exception:
        return []


def close_windows_for_pid(pid: int) -> int:
    """向目标进程所有顶层可见窗口发送 WM_CLOSE（优雅退出，保留会话）。"""
    if not sys.platform.startswith("win"):
        return 0
    try:
        user32 = ctypes.windll.user32
        hwnds = find_visible_windows_for_pid(pid)
        for hwnd in hwnds:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return len(hwnds)
    except Exception:
        return 0


def close_process_gracefully(proc, wait_seconds: float = 2.5) -> None:
    """供后台线程调用：优雅关闭进程，超时后温和 terminate，不阻塞主 UI。"""
    if proc is None or proc.poll() is not None:
        return
    close_windows_for_pid(proc.pid)
    steps = max(1, int(wait_seconds / 0.1))
    for _ in range(steps):
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def tile_windows_for_pids(pids: list[int], layout: str = "grid") -> int:
    """按网格或并排方式平铺多个受管浏览器窗口，返回成功排版的窗口数。"""
    if not sys.platform.startswith("win") or not pids:
        return 0
    hwnds: list[int] = []
    for pid in pids:
        ws = find_visible_windows_for_pid(pid)
        if ws:
            hwnds.append(ws[0])
    if not hwnds:
        return 0

    work_area = get_work_area()
    rects = compute_tile_rects(len(hwnds), work_area, layout=layout)
    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    placed = 0
    for hwnd, (x, y, w, h) in zip(hwnds, rects):
        try:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.MoveWindow(hwnd, x, y, w, h, True)
            placed += 1
        except Exception:
            pass
    return placed


def set_windows_visibility(pids: list[int], visible: bool) -> int:
    """一键隐藏或恢复所有受管浏览器窗口（老板键/托盘控制）。"""
    if not sys.platform.startswith("win") or not pids:
        return 0
    user32 = ctypes.windll.user32
    cmd = 9 if visible else 6  # SW_RESTORE = 9, SW_MINIMIZE = 6
    changed = 0
    for pid in pids:
        for hwnd in find_visible_windows_for_pid(pid):
            try:
                user32.ShowWindow(hwnd, cmd)
                changed += 1
            except Exception:
                pass
    return changed


def get_process_memory_mb(pid: int) -> float:
    """获取指定进程 Working Set 内存占用（MB），纯 ctypes 无外部依赖。"""
    if not sys.platform.startswith("win") or not pid:
        return 0.0
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        PROCESS_VM_READ = 0x0010

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, int(pid))
        if not hproc:
            return 0.0
        try:
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(pmc)
            if psapi.GetProcessMemoryInfo(hproc, ctypes.byref(pmc), pmc.cb):
                return round(pmc.WorkingSetSize / (1024 * 1024), 1)
        finally:
            kernel32.CloseHandle(hproc)
    except Exception:
        pass
    return 0.0


def calc_dir_size_mb(path: Path) -> float:
    """计算目录占用体积（MB），忽略权限异常。"""
    if not path.is_dir():
        return 0.0
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return round(total / (1024 * 1024), 1)
