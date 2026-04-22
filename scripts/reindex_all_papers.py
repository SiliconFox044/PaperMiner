"""将所有已完成论文从本地 chunks.json 重新索引到 Qdrant。

对每篇已完成的论文：删除旧 Qdrant 向量，然后从本地 chunks.json 重新 embed 并写入。
不会重新解析 PDF 或重新切分 chunk。
"""

import sys
import json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "Paper_RAG" / "config" / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_core.documents import Document
from Paper_RAG.pipeline.vector_store import (
    delete_paper_vectors,
    count_paper_vectors,
    add_chunks_to_vector_store,
    COLLECTION_NAME,
)
from Paper_RAG.registry.paper_registry import load_registry

DATA_DIR = Path(__file__).parent.parent / "data"


def reindex_all_papers() -> None:
    # ── Step 1: 收集所有已完成论文 ──────────────────────────────────────
    registry = load_registry()
    papers = registry.get("papers", {})
    completed = {
        pid: info for pid, info in papers.items()
        if info.get("status") == "completed"
    }
    print(f"共找到 {len(completed)} 篇已完成论文")

    # ── Step 2: 打印预览 ─────────────────────────────────────────────────
    print("\n=== 预览：chunks.json vs Qdrant 当前条数 ===")
    print(f"{'paper_id':<12} {'chunks.json':>12} {'Qdrant':>10} {'状态':>8}")
    print("-" * 50)

    preview_stats = []
    for pid in sorted(completed.keys()):
        chunks_path = DATA_DIR / "papers" / pid / "chunks.json"
        json_count = 0
        if chunks_path.exists():
            try:
                with open(chunks_path, encoding="utf-8") as f:
                    json_count = len(json.load(f))
            except Exception:
                pass

        try:
            qdrant_count = count_paper_vectors(
                paper_id=pid,
                data_dir=str(DATA_DIR)
            )
        except Exception:
            qdrant_count = -1

        status = "OK" if json_count == qdrant_count else "需修复"
        print(f"{pid:<12} {json_count:>12} {qdrant_count:>10} {status:>8}")
        preview_stats.append((pid, json_count, qdrant_count))

    # ── Step 3: 确认 ─────────────────────────────────────────────────────
    confirm = input("\n确认重新索引以上所有论文？输入 yes 继续：")
    if confirm.strip().lower() != "yes":
        print("已取消")
        return

    # ── Step 4: 逐个处理 ─────────────────────────────────────────────────
    success_count = 0
    skip_count = 0
    fail_count = 0

    print("\n=== 正在重新索引 ===")
    for pid, expected_count, _ in preview_stats:
        info = completed[pid]
        chunks_path = DATA_DIR / "papers" / pid / "chunks.json"

        # 4-1: 检查 chunks.json 是否存在
        if not chunks_path.exists():
            print(f"  [跳过] {pid} — chunks.json 不存在")
            skip_count += 1
            continue

        # 4-2: 构建 Document 对象列表
        try:
            with open(chunks_path, encoding="utf-8") as f:
                chunk_data = json.load(f)
        except Exception as e:
            print(f"  [失败] {pid} — 读取 chunks.json 出错: {e}")
            fail_count += 1
            continue

        documents = [
            Document(
                page_content=item["page_content"],
                metadata=item.get("metadata", {})
            )
            for item in chunk_data
        ]

        # 4-3: 删除旧 chunks
        try:
            delete_paper_vectors(paper_id=pid, data_dir=str(DATA_DIR))
        except Exception as e:
            print(f"  [失败] {pid} — 删除旧向量出错: {e}")
            fail_count += 1
            continue

        # 4-4: 重新写入（内部会重新 embed）
        try:
            add_chunks_to_vector_store(
                documents=documents,
                collection_name=COLLECTION_NAME,
                data_dir=str(DATA_DIR),
                paper_id=pid,
                source_filename=info.get("original_filename", ""),
            )
        except Exception as e:
            print(f"  [失败] {pid} — 写入新向量出错: {e}")
            fail_count += 1
            continue

        # 4-5: 验证写入结果
        try:
            new_count = count_paper_vectors(
                paper_id=pid,
                data_dir=str(DATA_DIR)
            )
            if new_count == expected_count:
                print(f"  [成功] {pid} — 期望 {expected_count} 条，实际 {new_count} 条 ✓")
                success_count += 1
            else:
                print(f"  [异常] {pid} — 期望 {expected_count} 条，实际 {new_count} 条 ✗")
                fail_count += 1
        except Exception as e:
            print(f"  [异常] {pid} — 验证计数出错: {e}")
            fail_count += 1

    # ── Step 5: 汇总 ─────────────────────────────────────────────────────
    total = success_count + skip_count + fail_count
    print(f"\n=== 汇总（共处理 {total} 篇）===")
    print(f"  成功：{success_count}")
    print(f"  跳过：{skip_count}")
    print(f"  失败：{fail_count}")


if __name__ == "__main__":
    reindex_all_papers()
