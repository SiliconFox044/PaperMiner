"""清理 Qdrant 向量库中的孤立 chunk。

删除 paper_id 不在 paper_registry.json 中的 chunk。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector
from Paper_RAG.pipeline.vector_store import COLLECTION_NAME
from Paper_RAG.registry.paper_registry import load_registry

DATA_DIR = Path(__file__).parent.parent / "data"
QDRANT_PATH = DATA_DIR / "qdrant"


def _get_paper_id(payload: dict) -> str | None:
    """从 payload 中提取 paper_id，兼容新旧两种格式。

    当前格式：  payload.paper_id
    旧版格式：  payload.metadata.paper_id（由 **doc.metadata 覆盖导致）
    """
    pid = payload.get("paper_id")
    if pid:
        return pid
    metadata = payload.get("metadata", {})
    if isinstance(metadata, dict):
        return metadata.get("paper_id")
    return None


def clean_orphan_chunks() -> None:
    # ── Step 1: 收集所有合法 paper_id ──────────────────────────────────────
    registry = load_registry()
    valid_ids = set(registry.get("papers", {}).keys())
    print(f"合法 paper_id 数量：{len(valid_ids)}")

    # ── Step 2: scroll 遍历 Qdrant 所有 chunks ─────────────────────────────
    client = QdrantClient(path=str(QDRANT_PATH))

    # 按 paper_id 聚合：{ paper_id: [point_id, ...] }
    paper_chunks: dict[str, list[str]] = {}
    offset: str | None = None

    print("正在遍历 Qdrant chunks...")
    while True:
        # scroll 返回 tuple: (points list, next_page_offset)
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=100,
            offset=offset,
            with_payload=True,
        )
        for point in points:
            pid = _get_paper_id(point.payload)
            if pid:
                paper_chunks.setdefault(pid, []).append(point.id)
        if offset is None:
            break

    total_points = sum(len(ids) for ids in paper_chunks.values())
    print(f"Qdrant 中共有 {total_points} 个 chunks，分布在 {len(paper_chunks)} 个 paper_id 下")

    # ── Step 3: 找出孤立 paper_id ─────────────────────────────────────────
    orphan_pids = {pid for pid in paper_chunks if pid not in valid_ids}

    # 统计合法 paper_id 下的 chunk 数量（用于发现重复）
    valid_pid_counts = {pid: len(ids) for pid, ids in paper_chunks.items() if pid in valid_ids}
    duplicate_pids = {pid: cnt for pid, cnt in valid_pid_counts.items() if cnt > 1}

    # ── Step 4: 打印预览 & 确认 ────────────────────────────────────────────
    print("\n=== 孤立 paper_id（将删除）===")
    if not orphan_pids:
        print("  无孤立 chunks，无需清理")
    else:
        for pid in sorted(orphan_pids):
            print(f"  {pid}: {len(paper_chunks[pid])} 个 chunks")

    print("\n=== 合法 paper_id chunk 分布（重复检测）===")
    if not duplicate_pids:
        print("  无重复")
    else:
        for pid, cnt in sorted(duplicate_pids.items(), key=lambda x: -x[1]):
            print(f"  {pid}: {cnt} 个 chunks")

    if not orphan_pids:
        print("\n无需删除，脚本退出")
        return

    confirm = input("\n确认删除以上孤立 chunks？输入 yes 继续：")
    if confirm.strip().lower() != "yes":
        print("已取消")
        return

    # ── Step 5: 执行删除 ──────────────────────────────────────────────────
    print("\n正在删除孤立 chunks...")
    total_deleted = 0
    for pid in orphan_pids:
        point_ids = paper_chunks[pid]
        deleted_count = 0
        # 尝试两种 key：顶层 paper_id 和 legacy metadata.paper_id
        for key in ("paper_id", "metadata.paper_id"):
            result = client.delete(
                collection_name=COLLECTION_NAME,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key=key,
                                match=MatchValue(value=pid)
                            )
                        ]
                    )
                )
            )
            # client.delete 返回 DeleteOperationResult，deleted 字段是实际删除数
            d = getattr(result, "deleted", 0) or 0
            deleted_count += d
        total_deleted += deleted_count
        print(f"  已删除 paper_id={pid}，共 {deleted_count} 个 chunks（预期 {len(point_ids)} 个）")

    # ── Step 6: 打印摘要 ──────────────────────────────────────────────────
    print(f"\n=== 清理完成 ===")
    print(f"删除孤立 paper_id 数量：{len(orphan_pids)}")
    print(f"删除 chunks 总数：{total_deleted}")


if __name__ == "__main__":
    clean_orphan_chunks()
