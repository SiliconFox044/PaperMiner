"""Batch Processor 模块 - Legal RAG 的并发 PDF 处理。

通过 RAG pipeline 对多个 PDF 论文提供并发批处理。
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional

from Paper_RAG.registry.paper_registry import (
    load_registry,
    save_registry,
    register_paper,
    update_status,
    delete_paper,
)
from Paper_RAG.pipeline import pdf_parser, text_cleaner, chunk_splitter, embedding, vector_store
from Paper_RAG.registry.md5_records import remove_md5_by_paper_id


DATA_DIR = Path(__file__).parent.parent.parent / "data"
LOCK = threading.Lock()


# ── PDF 收集 ─────────────────────────────────────────────────────────────


def collect_pdfs(paths: list[str], recursive: bool = False, max_depth: int = 1) -> List[Path]:
    """从给定路径（文件或目录列表）中收集所有 PDF 文件路径。

    Args:
        paths: 文件路径和/或目录路径列表。
        recursive: 若为 False，则仅处理每个目录的顶层。
        max_depth: 当 recursive=True 时，最大下降目录深度。
                   max_depth=1 表示仅处理直接子项。
                   max_depth=999 视为"无限"。

    Returns:
        所有发现的 PDF 文件的去重排序 Path 对象列表。
    """
    if max_depth >= 999:
        max_depth = 999  # 视为无限深度

    results: List[Path] = []

    for path_str in paths:
        p = Path(path_str)
        if not p.exists():
            continue

        if p.is_file():
            if p.suffix.lower() == ".pdf":
                results.append(p)
        elif p.is_dir():
            _collect_from_dir(p, results, recursive=recursive, depth=1, max_depth=max_depth)

    # 去重并排序
    unique = list({rp.resolve(): rp for rp in results}.values())
    return sorted(unique, key=lambda x: x.name)


def _collect_from_dir(
    directory: Path,
    out_list: List[Path],
    recursive: bool,
    depth: int,
    max_depth: int
) -> None:
    """递归从目录中收集 PDF，最多到 max_depth 层。"""
    try:
        entries = list(directory.iterdir())
    except PermissionError:
        return

    for entry in entries:
        if entry.is_file() and entry.suffix.lower() == ".pdf":
            out_list.append(entry)
        elif entry.is_dir() and recursive and depth < max_depth:
            _collect_from_dir(entry, out_list, recursive=True, depth=depth + 1, max_depth=max_depth)


# ── 单篇论文处理 ─────────────────────────────────────────────────────────────


def process_single_paper(pdf_path: Path, registry: dict, mineru_mode: str = "flash") -> dict:
    """对单篇 PDF 论文执行完整 RAG pipeline。

    Args:
        pdf_path: PDF 文件路径。
        registry: 共享的论文注册表字典（就地修改）。

    Returns:
        包含以下键的字典：status ("completed" | "failed" | "skipped")、
        filename、chunk_count（可选）、error_msg（可选）、reason（可选）。
    """
    # 步骤 1：注册论文
    try:
        paper_id = register_paper(registry, str(pdf_path))
    except ValueError as e:
        if str(e) == "already_exists":
            return {
                "status": "skipped",
                "reason": "already_exists",
                "filename": pdf_path.name,
            }
        raise

    # Step 1.5: 清除该 paper_id 在 Qdrant 中的旧 chunks（防止重试时产生重复）
    try:
        existing_count = vector_store.count_paper_vectors(
            paper_id=paper_id,
            data_dir=str(DATA_DIR)
        )
        if existing_count > 0:
            vector_store.delete_paper_vectors(
                paper_id=paper_id,
                data_dir=str(DATA_DIR)
            )
    except Exception:
        pass  # 清理失败不阻断主流程

    # 步骤 2：标记为处理中
    update_status(registry, paper_id, "processing")

    try:
        # 步骤 3：解析 PDF → markdown
        markdown = pdf_parser.parse_pdf(str(pdf_path), mode=mineru_mode)

        # 步骤 4：清洗 markdown
        cleaned = text_cleaner.clean_markdown(markdown)

        # 步骤 5：切分为 chunks
        chunks = chunk_splitter.split_chunks(cleaned)

        # 步骤 6：Embed chunks
        texts = [c.page_content for c in chunks]
        vectors = embedding.embed_documents_batch(
            documents=texts,
            batch_size=50,
            pdf_path=str(pdf_path),
            data_dir=str(DATA_DIR),
        )

        # 步骤 7：存储向量到 Qdrant
        vector_store.add_chunks_to_vector_store(
            documents=chunks,
            paper_id=paper_id,
            source_filename=pdf_path.name,
            collection_name=vector_store.COLLECTION_NAME,
            data_dir=str(DATA_DIR),
        )

        # 步骤 8：将 chunks markdown 保存到本地文件
        chunks_dir = DATA_DIR / "papers" / paper_id
        chunks_dir.mkdir(parents=True, exist_ok=True)
        chunks_json_path = chunks_dir / "chunks.json"
        chunk_data = [
            {"page_content": c.page_content, "metadata": c.metadata}
            for c in chunks
        ]
        with open(chunks_json_path, "w", encoding="utf-8") as f:
            json.dump(chunk_data, f, ensure_ascii=False, indent=2)

        # 步骤 9：标记为已完成
        update_status(registry, paper_id, "completed", chunk_count=len(chunks))

        return {
            "status": "completed",
            "filename": pdf_path.name,
            "paper_id": paper_id,
            "chunk_count": len(chunks),
        }

    except Exception as e:
        update_status(registry, paper_id, "failed", error_msg=str(e))
        return {
            "status": "failed",
            "filename": pdf_path.name,
            "paper_id": paper_id,
            "error_msg": str(e),
        }


# ── 批处理 ─────────────────────────────────────────────────────────────────


def batch_process(
    pdf_paths: List[Path],
    progress_callback: Callable[[dict], None],
    max_workers: int = 3,
    mineru_mode: str = "flash",
) -> List[dict]:
    """并发处理多个 PDF 论文。

    Args:
        pdf_paths: 要处理的 PDF 文件路径列表。
        progress_callback: 每篇论文处理完成后立即调用，传入结果字典。
        max_workers: 最大并发线程数。

    Returns:
        按完成顺序排列的所有结果字典列表。
    """
    results: List[dict] = []
    registry = load_registry()

    def worker(pdf_path: Path) -> dict:
        # 每个线程处理自己的论文；registry 写操作由锁保护
        result = process_single_paper(pdf_path, registry, mineru_mode=mineru_mode)
        with LOCK:
            save_registry(registry)
        return result

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, p): p for p in pdf_paths}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as e:
                pdf_path = futures[future]
                result = {
                    "status": "failed",
                    "filename": pdf_path.name,
                    "error_msg": str(e),
                }
            results.append(result)
            progress_callback(result)

    return results


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
    except Exception as e:
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
    except Exception as e:
        vectors_after = -1

    # ── 步骤 5：删除本地文件 ────────────────────────────────────────
    import shutil
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


def batch_delete(
    paper_ids: list[str],
    registry: dict,
    progress_callback: Callable[[dict], None],
) -> list[dict]:
    """串行删除多篇论文。

    Args:
        paper_ids: 要删除的论文 ID 列表。
        registry: 共享的论文注册表字典。
        progress_callback: 每篇论文删除完成后调用，传入结果字典。

    Returns:
        按完成顺序排列的所有结果字典列表。
    """
    results: list[dict] = []

    for paper_id in paper_ids:
        result = delete_single_paper(paper_id, registry)
        results.append(result)
        progress_callback(result)

    return results
