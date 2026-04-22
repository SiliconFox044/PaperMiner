"""用于读取和更新 MD5 去重记录的工具函数。"""

import json
import os
from filelock import FileLock


DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
MD5_RECORDS_PATH = os.path.join(DATA_DIR, "md5_records.json")


def _lock_path():
    """返回 md5_records 锁文件的路径。"""
    return os.path.join(DATA_DIR, "md5_records.json.lock")


# 模块级共享锁实例，is_singleton=True 允许同线程重入
_lock = FileLock(_lock_path(), is_singleton=True)


def load_md5_records() -> dict:
    """从文件中加载 MD5 记录（线程安全）。"""
    with _lock:
        if not os.path.exists(MD5_RECORDS_PATH):
            return {}
        with open(MD5_RECORDS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)


def save_md5_records(records: dict) -> None:
    """将 MD5 记录保存到文件（线程安全，原子写入）。"""
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_path = MD5_RECORDS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, MD5_RECORDS_PATH)


def remove_md5_by_paper_id(paper_id: str) -> tuple[bool, str]:
    """删除 md5_records 中所有以指定 paper_id 开头的条目（线程安全）。"""
    with _lock:
        if not os.path.exists(MD5_RECORDS_PATH):
            return True, "md5_records 文件不存在（已跳过）"

        try:
            records = load_md5_records()
        except Exception as e:
            return False, f"md5_records 读取失败：{e}"

        matched_keys = [k for k in records if k.startswith(paper_id)]
        if not matched_keys:
            return True, "无对应 md5 记录（已跳过）"

        for k in matched_keys:
            del records[k]

        try:
            save_md5_records(records)
            return True, f"已删除 {len(matched_keys)} 条 md5 记录"
        except Exception as e:
            return False, f"md5_records 保存失败：{e}"
