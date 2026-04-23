"""文档注册模块 - 管理文档注册与状态追踪。

管理 data/paper_registry.json，追踪 RAG pipeline 中每篇文档的处理状态与元数据。
"""

import json
import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
import uuid

import shutil
from Paper_RAG.pipeline import vector_store
from Paper_RAG.registry.md5_records import remove_md5_by_paper_id

from filelock import FileLock


# 注册表文件路径（相对于项目根目录）
_REGISTRY_FILENAME = "paper_registry.json"


def _registry_path() -> Path:
    """返回 paper_registry.json 的绝对路径。"""
    # Paper_RAG/registry/paper_registry.py → 项目根目录
    return Path(__file__).parent.parent.parent / "data" / _REGISTRY_FILENAME


def _lock_path() -> Path:
    """返回注册表锁文件的路径。"""
    return _registry_path().with_suffix(".json.lock")


# 模块级共享锁实例，is_singleton=True 允许同线程重入
_lock = FileLock(_lock_path(), is_singleton=True)


# ── 公开 API ────────────────────────────────────────────────────────────────


def load_registry() -> dict:
    """从 data/paper_registry.json 加载文档注册表。

    Returns:
        包含 "folders" 和 "papers" 键的字典。
        若文件不存在，返回仅含 "uncategorized" 系统文件夹的默认结构。
        若文件存在但缺少 "folders" 键，则自动以默认值初始化。
    """
    with _lock:
        path = _registry_path()
        if not path.exists():
            return _default_registry()

        with open(path, "r", encoding="utf-8-sig") as f:
            registry = json.load(f)

        # 向后兼容：若缺少 folders 键则自动添加
        if "folders" not in registry:
            registry["folders"] = _default_registry()["folders"]
            save_registry(registry)

        # 确保所有文档都包含 folder_id 字段
        if "papers" not in registry:
            # 在重构前对顶层键做快照，避免在自身上迭代引起问题
            legacy_papers = dict(registry)  # shallow copy of top-level items
            # 删除非文档条目的元数据键
            for _key in ("folders",):
                legacy_papers.pop(_key, None)
            registry["papers"] = legacy_papers
            for pid, paper in list(legacy_papers.items()):
                if isinstance(paper, dict):
                    paper.setdefault("folder_id", "uncategorized")
            # 将非 folders 键移出
            top_level_folders = registry.pop("folders", _default_registry()["folders"])
            # 恢复 folders 键，供后续代码使用
            registry["folders"] = top_level_folders

        default_folders = _default_registry()["folders"]
        for fid, fdata in list(default_folders.items()):
            if fid not in registry["folders"]:
                registry["folders"][fid] = fdata

        for pid, paper in registry.get("papers", {}).items():
            if isinstance(paper, dict):
                paper.setdefault("folder_id", "uncategorized")

        save_registry(registry)
        return registry


def _default_registry() -> dict:
    """返回包含系统文件夹的默认注册表结构。"""
    return {
        "folders": {
            "uncategorized": {
                "id": "uncategorized",
                "name": "未归类",
                "parent_id": None,
                "is_system": True,
                "created_at": "2026-01-01T00:00:00"
            }
        },
        "papers": {}
    }


def save_registry(registry: dict) -> None:
    """将文档注册表保存到 data/paper_registry.json。

    采用原子写入（先写临时文件再替换）防止进程中途崩溃导致 JSON 损坏。
    使用 FileLock 防止并发写入冲突。

    Args:
        registry: 包含 "folders" 和 "papers" 键的字典。
    """
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def register_paper(registry: dict, pdf_path: str) -> str:
    """注册新文档，或重新注册失败/处理中的文档。

    Args:
        registry: 当前文档注册表（原地修改）。
        pdf_path: PDF 文件的绝对路径。

    Returns:
        分配的 paper_id（MD5 前缀，取前 8 位字符）。

    Raises:
        ValueError: 若相同内容的文档已存在且状态为 "completed"。
    """
    # 根据文件内容的 MD5 计算 paper_id
    md5_hash = _compute_md5(pdf_path)
    paper_id = md5_hash[:8]

    papers = registry.get("papers", registry)

    if paper_id in papers:
        existing = papers[paper_id]
        if existing.get("status") in ("completed", "processing"):
            raise ValueError("already_exists")
        # 允许重新注册：适用于状态为 "failed" 或 "processing" 的条目

    original_filename = os.path.basename(pdf_path)
    local_dir = f"data/papers/{paper_id}/"

    papers[paper_id] = {
        "paper_id": paper_id,
        "original_filename": original_filename,
        "pdf_path": os.path.abspath(pdf_path),
        "local_dir": local_dir,
        "status": "pending",
        "chunk_count": 0,
        "registered_at": _iso_now(),
        "completed_at": None,
        "error_msg": None,
        "folder_id": "uncategorized",
    }

    return paper_id


def update_status(registry: dict, paper_id: str, status: str, **kwargs) -> None:
    """更新已注册文档的状态及可选字段。

    Args:
        registry: 当前文档注册表（原地修改）。
        paper_id: 文档 ID。
        status: 新状态，合法值："pending" / "processing" / "completed" / "failed" / "deleting" / "delete_failed"。
        **kwargs: 需额外更新的字段（如 chunk_count、error_msg）。

    Raises:
        KeyError: 若 paper_id 在注册表中不存在。
    """
    papers = registry.get("papers", registry)
    if paper_id not in papers:
        raise KeyError(f"paper_id '{paper_id}' not found in registry")

    if status not in ("pending", "processing", "completed", "failed", "deleting", "delete_failed"):
        raise ValueError(f"Invalid status: {status}")

    papers[paper_id]["status"] = status

    if status == "completed":
        papers[paper_id]["completed_at"] = _iso_now()

    for key, value in kwargs.items():
        papers[paper_id][key] = value


def update_paper_status(
    paper_id: str,
    status: str,
    chunk_count: int = None,
    error_msg: str = None,
) -> None:
    """通过 paper_id 直接更新文档状态（自动加载并保存注册表）。

    Args:
        paper_id: 文档 ID。
        status: 新状态。
        chunk_count: 可选，要记录的 chunk 数量。
        error_msg: 可选，要记录的错误信息。
    """
    registry = load_registry()
    papers = registry.get("papers", registry)
    if paper_id not in papers:
        # 条目已被删除 —— 静默跳过
        return
    kwargs = {}
    if chunk_count is not None:
        kwargs["chunk_count"] = chunk_count
    if error_msg is not None:
        kwargs["error_msg"] = error_msg
    update_status(registry, paper_id, status, **kwargs)
    save_registry(registry)


def delete_paper(registry: dict, paper_id: str) -> None:
    """在注册表中将文档标记为已删除（软删除）。

    Args:
        registry: 当前文档注册表（原地修改）。
        paper_id: 文档 ID。

    Raises:
        KeyError: 若 paper_id 在注册表中不存在。
    """
    papers = registry.get("papers", registry)
    if paper_id not in papers:
        raise KeyError(f"paper_id '{paper_id}' not found in registry")

    papers[paper_id]["status"] = "deleted"
    papers[paper_id]["deleted_at"] = _iso_now()



# ── 文件夹管理 ─────────────────────────────────────────────────────────


def create_folder(name: str, parent_id: str = None) -> dict:
    """创建新文件夹。

    Args:
        name: 文件夹显示名称。
        parent_id: 父文件夹 ID，None 表示根级别。

    Returns:
        新创建的文件夹对象。
    """
    registry = load_registry()
    folder_id = uuid.uuid4().hex[:8]

    folder = {
        "id": folder_id,
        "name": name,
        "parent_id": parent_id,
        "is_system": False,
        "created_at": _iso_now()
    }

    registry["folders"][folder_id] = folder
    save_registry(registry)

    return folder


def rename_folder(folder_id: str, new_name: str) -> dict:
    """重命名文件夹。

    Args:
        folder_id: 要重命名的文件夹 ID。
        new_name: 新显示名称。

    Returns:
        更新后的文件夹对象。

    Raises:
        ValueError: 若尝试重命名系统文件夹。
        KeyError: 若 folder_id 不存在。
    """
    if folder_id == "uncategorized":
        raise ValueError("Cannot rename system folder 'uncategorized'")

    registry = load_registry()
    if folder_id not in registry["folders"]:
        raise KeyError(f"folder_id '{folder_id}' not found")

    registry["folders"][folder_id]["name"] = new_name
    folder = registry["folders"][folder_id]
    save_registry(registry)

    return folder


def delete_folder(folder_id: str) -> dict:
    """删除文件夹。

    将该文件夹（及其子文件夹）中的所有文档移入 "uncategorized"。
    递归删除所有子文件夹。

    Args:
        folder_id: 要删除的文件夹 ID。

    Returns:
        {"deleted_folders": [...], "moved_papers": [...]}

    Raises:
        ValueError: 若尝试删除系统文件夹。
    """
    if folder_id == "uncategorized":
        raise ValueError("Cannot delete system folder 'uncategorized'")

    registry = load_registry()

    # 收集所有需删除的文件夹 ID（包含 folder_id 及其所有子孙）
    folder_ids_to_delete = _collect_descendant_folder_ids(folder_id, registry["folders"])
    folder_ids_to_delete.add(folder_id)

    # 收集所有将被移动的文档 ID
    papers = registry.get("papers", {})
    moved_papers = []
    for pid, paper in papers.items():
        if paper.get("folder_id") in folder_ids_to_delete:
            paper["folder_id"] = "uncategorized"
            moved_papers.append(pid)

    # 删除文件夹
    deleted_folders = []
    for fid in folder_ids_to_delete:
        if fid in registry["folders"]:
            deleted_folders.append(fid)
            del registry["folders"][fid]

    save_registry(registry)

    return {
        "deleted_folders": deleted_folders,
        "moved_papers": moved_papers
    }


def _collect_descendant_folder_ids(folder_id: str, folders: dict) -> set:
    """递归收集所有子孙文件夹的 ID。"""
    descendants = set()
    for fid, fdata in list(folders.items()):
        if fdata.get("parent_id") == folder_id:
            descendants.add(fid)
            descendants.update(_collect_descendant_folder_ids(fid, folders))
    return descendants


def move_paper_to_folder(paper_id: str, folder_id: str) -> dict:
    """将文档移动到指定文件夹。

    Args:
        paper_id: 要移动的文档 ID。
        folder_id: 目标文件夹 ID，必须已存在。

    Returns:
        更新后的文档对象。

    Raises:
        KeyError: 若 paper_id 不存在。
        ValueError: 若 folder_id 不存在。
    """
    registry = load_registry()

    papers = registry.get("papers", {})
    if paper_id not in papers:
        raise KeyError(f"paper_id '{paper_id}' not found")

    if folder_id not in registry["folders"]:
        raise ValueError(f"folder_id '{folder_id}' does not exist")

    papers[paper_id]["folder_id"] = folder_id
    paper = papers[paper_id]
    save_registry(registry)

    return paper


def get_folder_tree() -> list:
    """返回嵌套的文件夹树结构。

    "uncategorized" 作为系统文件夹始终排在最前。
    每个节点包含：id、name、parent_id、is_system、children[]

    Returns:
        根文件夹节点列表（子节点嵌套在内部）。
    """
    registry = load_registry()
    folders = dict(registry["folders"])

    # 构建子节点映射表
    children_map: dict = {}
    for fid, fdata in folders.items():
        parent = fdata.get("parent_id")
        if parent not in children_map:
            children_map[parent] = []
        children_map[parent].append(fid)

    def build_node(fid: str) -> dict:
        fdata = folders[fid]
        node = {
            "id": fdata["id"],
            "name": fdata["name"],
            "parent_id": fdata.get("parent_id"),
            "is_system": fdata.get("is_system", False),
            "children": []
        }
        for child_id in children_map.get(fid, []):
            node["children"].append(build_node(child_id))
        return node

    # uncategorized 始终排在最前，其后为其他根文件夹
    root_folders = children_map.get(None, [])
    tree = []
    if "uncategorized" in folders:
        tree.append(build_node("uncategorized"))
    for fid in sorted(root_folders):
        if fid != "uncategorized":
            tree.append(build_node(fid))

    return tree


# ── 内部辅助函数 ──────────────────────────────────────────────────────────


def _compute_md5(file_path: str) -> str:
    """计算文件内容的 MD5 十六进制摘要。"""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def _iso_now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.utcnow().isoformat()


# ── 论文删除 ────────────────────────────────────────────────────────────────


DATA_DIR = Path(__file__).parent.parent.parent / "data"


def delete_single_paper(paper_id: str, registry: dict) -> dict:
    """删除单篇论文，含前置检查和后置验证。

    执行顺序：Qdrant 向量 → 本地文件 → registry 更新
    返回包含详细验证信息的结果字典。

    结果字典键说明：
        status:          "completed" | "partial" | "failed"
        paper_id:        str
        filename:        str
        chunk_count:     int（来自 registry，为预期数量）
        vectors_before:  int（删除前实际找到的向量数）
        vectors_after:   int（删除后剩余的向量数）
        local_deleted:   bool（本地文件夹是否已删除）
        error_msg:       str | None
        detail:          str（人类可读的操作摘要）
    """
    # ── 步骤 1：查询 registry ──────────────────────────────────────────
    papers = registry.get("papers", registry)
    info = papers.get(paper_id)
    if info is None:
        return {
            "status": "failed",
            "paper_id": paper_id,
            "filename": "unknown",
            "chunk_count": 0,
            "vectors_before": 0,
            "vectors_after": 0,
            "local_deleted": False,
            "error_msg": "paper_id not found in registry",
            "detail": "✗ 删除失败：registry 中找不到该论文记录",
        }

    original_filename = info.get("original_filename", "unknown")
    local_dir = info.get("local_dir", "")
    chunk_count = info.get("chunk_count", 0)

    # ── 步骤 2：前置检查 — 删除前统计向量数 ─────────────────────────
    try:
        vectors_before = vector_store.count_paper_vectors(
            paper_id=paper_id,
            data_dir=str(DATA_DIR)
        )
    except Exception:
        vectors_before = -1  # -1 表示计数失败

    # ── 步骤 3：删除 Qdrant 向量 ─────────────────────────────────────
    try:
        vector_store.delete_paper_vectors(
            paper_id=paper_id,
            data_dir=str(DATA_DIR)
        )
    except Exception as e:
        return {
            "status": "failed",
            "paper_id": paper_id,
            "filename": original_filename,
            "chunk_count": chunk_count,
            "vectors_before": vectors_before,
            "vectors_after": vectors_before,
            "local_deleted": False,
            "error_msg": str(e),
            "detail": f"✗ 删除失败：Qdrant 操作出错 — {e}",
        }

    # ── 步骤 4：后置检查 — 删除后统计向量数 ─────────────────────────
    try:
        vectors_after = vector_store.count_paper_vectors(
            paper_id=paper_id,
            data_dir=str(DATA_DIR)
        )
    except Exception:
        vectors_after = -1

    # ── 步骤 5：删除本地文件 ────────────────────────────────────────
    local_deleted = False
    if local_dir:
        local_path = DATA_DIR / "papers" / paper_id
        if local_path.exists():
            shutil.rmtree(local_path, ignore_errors=True)
            local_deleted = not local_path.exists()
        else:
            local_deleted = False  # 文件夹本来就不存在

    # ── 步骤 5.5：清理 md5_records ─────────────────────────────────────
    md5_ok, md5_msg = remove_md5_by_paper_id(paper_id)

    # ── 步骤 6：更新 registry ───────────────────────────────────────────
    delete_paper(registry, paper_id)
    save_registry(registry)

    # ── 步骤 7：构建包含 detail 字符串的结果 ───────────────────────────
    # 判断向量删除结果
    if vectors_before == 0:
        vector_msg = "向量不存在（已跳过）"
        vector_ok = True
    elif vectors_before == -1:
        vector_msg = "向量计数失败（无法验证）"
        vector_ok = False
    elif vectors_after == 0:
        vector_msg = f"向量已清除（{vectors_before} 条）"
        vector_ok = True
    elif vectors_after > 0:
        vector_msg = f"向量未完全清除（删除前 {vectors_before} 条，删除后仍剩 {vectors_after} 条）"
        vector_ok = False
    else:
        vector_msg = f"向量已清除（{vectors_before} 条）"
        vector_ok = True

    # 判断本地文件结果
    if not local_dir:
        local_msg = "无本地文件记录（已跳过）"
    elif local_deleted:
        local_msg = "本地文件已删除"
    else:
        local_msg = "本地文件不存在（已跳过）"

    # 判断整体状态
    if vector_ok and md5_ok:
        status = "completed"
        prefix = "✓ 完整删除" if vectors_before > 0 else "⚠ 部分成功"
    else:
        status = "partial"
        prefix = "⚠ 部分成功"

    detail = f"{prefix}    {vector_msg}，{local_msg}，{md5_msg}，记录已更新"

    return {
        "status": status,
        "paper_id": paper_id,
        "filename": original_filename,
        "chunk_count": chunk_count,
        "vectors_before": vectors_before,
        "vectors_after": vectors_after,
        "local_deleted": local_deleted,
        "md5_deleted": md5_ok,
        "md5_msg": md5_msg,
        "error_msg": None,
        "detail": detail,
    }
