"""重试清理工具：清理失败条目的所有中间数据，为重试做准备。"""

import logging
import os

from Paper_RAG.pipeline.vector_store import delete_paper_vectors
from Paper_RAG.registry.md5_records import remove_md5_by_paper_id
from Paper_RAG.registry.paper_registry import load_registry, save_registry, update_paper_status

logger = logging.getLogger(__name__)


def cleanup_for_retry(paper_id: str) -> dict:
    """清理失败条目的所有中间数据，为重试做准备。

    执行顺序：
    1. 清理 Qdrant 向量（硬删除）
    2. 删除派生文件，保留 source.pdf
    3. 清理 md5_records
    4. 将 registry 状态重置为 processing，清空 error_msg

    每一步用 try/except 包裹，任何步骤失败记录错误后继续执行后续步骤。

    Returns:
        {"success": bool, "paper_id": str, "steps": {...}}
    """
    result = {
        "success": False,
        "paper_id": paper_id,
        "steps": {}
    }

    # ── Step 1: 清理 Qdrant 向量 ─────────────────────────────────────────────
    try:
        delete_paper_vectors(paper_id)
        result["steps"]["qdrant"] = {"ok": True, "msg": "向量已清理"}
    except Exception as e:
        logger.warning(f"[{paper_id}] Qdrant 清理失败（继续执行）: {e}")
        result["steps"]["qdrant"] = {"ok": False, "msg": str(e)}

    # ── Step 2: 删除派生文件，保留 source.pdf ─────────────────────────────────
    try:
        paper_dir = os.path.join("data", "papers", paper_id)
        if os.path.exists(paper_dir):
            for filename in os.listdir(paper_dir):
                if filename != "source.pdf":
                    file_path = os.path.join(paper_dir, filename)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
            result["steps"]["files"] = {"ok": True, "msg": "派生文件已清理，source.pdf 保留"}
        else:
            result["steps"]["files"] = {"ok": True, "msg": "目录不存在（已跳过）"}
    except Exception as e:
        logger.warning(f"[{paper_id}] 文件清理失败（继续执行）: {e}")
        result["steps"]["files"] = {"ok": False, "msg": str(e)}

    # ── Step 3: 清理 md5_records ──────────────────────────────────────────────
    try:
        ok, msg = remove_md5_by_paper_id(paper_id)
        result["steps"]["md5"] = {"ok": ok, "msg": msg}
    except Exception as e:
        logger.warning(f"[{paper_id}] md5_records 清理失败（继续执行）: {e}")
        result["steps"]["md5"] = {"ok": False, "msg": str(e)}

    # ── Step 4: 重置 registry 状态为 processing，清空 error_msg ───────────────
    try:
        registry = load_registry()
        papers = registry.get("papers", registry)
        if paper_id in papers:
            papers[paper_id]["status"] = "processing"
            papers[paper_id]["error_msg"] = None
            # 清除旧 task_token（如果存在）
            papers[paper_id].pop("task_token", None)
            save_registry(registry)
            result["steps"]["registry"] = {"ok": True, "msg": "状态已重置为 processing"}
        else:
            result["steps"]["registry"] = {"ok": False, "msg": "条目不存在于 registry"}
    except Exception as e:
        logger.warning(f"[{paper_id}] registry 重置失败（继续执行）: {e}")
        result["steps"]["registry"] = {"ok": False, "msg": str(e)}

    # 汇总：只要 registry 重置成功就算基本完成
    result["success"] = result["steps"].get("registry", {}).get("ok", False)
    return result
