"""Inspector 模块 - Pipeline 可观测性系统。

为每个 pipeline 阶段提供 checkpoint 保存与诊断检查功能。
- 日志记录：终端输出 + logs/pipeline.log
- Checkpoint：data/checkpoints/*.json
- 各阶段对应的检查函数
"""

import os
import sys
import json
import logging
from datetime import datetime
from typing import Any, List, Dict

# 确保日志目录存在
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
CHECKPOINT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "checkpoints")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(CHECKPOINT_DIR, exist_ok=True)


def setup_logging(module_name: str = "pipeline") -> logging.Logger:
    """配置日志，同时输出到终端和文件。

    Args:
        module_name: logger 名称

    Returns:
        已配置的 logger 实例
    """
    logger = logging.getLogger(module_name)
    logger.setLevel(logging.DEBUG)

    # 避免重复添加 handler
    if not logger.handlers:
        # 终端 handler（INFO 级别）
        terminal_handler = logging.StreamHandler(sys.stdout)
        terminal_handler.setLevel(logging.INFO)
        terminal_formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s - %(message)s",
            datefmt="%H:%M:%S"
        )
        terminal_handler.setFormatter(terminal_formatter)

        # 文件 handler（DEBUG 级别）
        file_handler = logging.FileHandler(
            os.path.join(LOG_DIR, "pipeline.log"),
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            "[%(asctime)s] [%(name)s] %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)

        logger.addHandler(terminal_handler)
        logger.addHandler(file_handler)

    return logger


# 全局 logger 实例
logger = setup_logging()


def _serialize_for_json(obj: Any) -> Any:
    """将不可 JSON 序列化的对象转换为兼容类型。

    Args:
        obj: 待序列化的对象

    Returns:
        JSON 兼容的表示形式
    """
    import numpy as np

    if isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    elif hasattr(obj, "__dict__"):
        return str(obj)
    else:
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)


def save_checkpoint(stage_name: str, data: Any, output_dir: str = CHECKPOINT_DIR) -> str:
    """将 checkpoint 数据保存为 JSON 文件。

    文件名格式："01_parsed.json"（按阶段带序号前缀）

    Args:
        stage_name: pipeline 阶段名称（如 "parsed"、"cleaned"、"chunks"）
        data: 待序列化的数据（转换后须为 JSON 兼容格式）
        output_dir: checkpoint 文件的保存目录

    Returns:
        已保存的 checkpoint 文件路径
    """
    os.makedirs(output_dir, exist_ok=True)

    # 阶段名称到序号的映射
    stage_numbers = {
        "parsed": "01",
        "cleaned": "02",
        "chunks": "03",
        "embeddings": "04",
    }

    seq_num = stage_numbers.get(stage_name, "00")
    filename = f"{seq_num}_{stage_name}.json"
    filepath = os.path.join(output_dir, filename)

    try:
        # 先尝试直接序列化
        serialized = _serialize_for_json(data)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(serialized, f, ensure_ascii=False, indent=2)

        logger.info(f"[CHECKPOINT] Saved {stage_name} → {filepath}")
        return filepath

    except Exception as e:
        logger.error(f"[CHECKPOINT] Failed to save {stage_name}: {e}")
        # 保存为错误标记文件
        error_data = {"error": str(e), "stage": stage_name}
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(error_data, f, ensure_ascii=False)
        return filepath


# =============================================================================
# 各阶段检查函数
# =============================================================================


def inspect_parsed(markdown_text: str, source_file: str = "") -> None:
    """检查并记录 PDF 解析输出结果。

    注意：pdf_parser 返回的是 Markdown 字符串而非文档列表。
    此处通过检查 Markdown 结构作为替代手段。

    Args:
        markdown_text: MinerU 输出的原始 Markdown
        source_file: 源 PDF 文件名
    """
    try:
        if not markdown_text:
            logger.warning("[PARSED] Empty output!")
            return

        # 按双换行符估算块数（近似页数）
        chunks = markdown_text.split("\n\n")
        chunk_count = len(chunks)

        # 字符统计
        total_chars = len(markdown_text)

        # 标题数量统计
        h1_count = markdown_text.count("\n# ")
        h2_count = markdown_text.count("\n## ")
        h3_count = markdown_text.count("\n### ")

        logger.info("=" * 60)
        logger.info("[PARSED] MinerU 解析结果")
        logger.info("=" * 60)
        logger.info(f"  源文件: {source_file or 'N/A'}")
        logger.info(f"  估算块数: {chunk_count}")
        logger.info(f"  总字符数: {total_chars:,}")
        logger.info(f"  H1 标题: {h1_count}, H2: {h2_count}, H3: {h3_count}")

        # 预览前 3 块内容（每块最多 200 字符）
        logger.info("-" * 60)
        logger.info("[PARSED] 前3块内容预览:")
        for i, chunk in enumerate(chunks[:3]):
            preview = chunk[:200].replace("\n", " ")
            logger.info(f"  块 {i+1}: {preview}...")

        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"[PARSED] Inspect failed: {e}")


def inspect_cleaned(raw_text: str, cleaned_text: str) -> None:
    """检查 Markdown 清洗后的输出结果。

    Args:
        raw_text: 清洗前的原始 Markdown
        cleaned_text: 处理后的清洗 Markdown
    """
    try:
        raw_chars = len(raw_text)
        cleaned_chars = len(cleaned_text)
        removed_chars = raw_chars - cleaned_chars
        removal_pct = (removed_chars / raw_chars * 100) if raw_chars > 0 else 0

        # 统计各类元素数量
        raw_lines = raw_text.split("\n")
        cleaned_lines = cleaned_text.split("\n")

        logger.info("=" * 60)
        logger.info("[CLEANED] 清洗结果")
        logger.info("=" * 60)
        logger.info(f"  清洗前: {raw_chars:,} 字符, {len(raw_lines)} 行")
        logger.info(f"  清洗后: {cleaned_chars:,} 字符, {len(cleaned_lines)} 行")
        logger.info(f"  移除: {removed_chars:,} 字符 ({removal_pct:.1f}%)")

        # 预览清洗后的前几块内容
        cleaned_chunks = cleaned_text.split("\n\n")
        logger.info("-" * 60)
        logger.info("[CLEANED] 前3块内容预览:")
        for i, chunk in enumerate(cleaned_chunks[:3]):
            preview = chunk[:200].replace("\n", " ")
            logger.info(f"  块 {i+1}: {preview}...")

        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"[CLEANED] Inspect failed: {e}")


def inspect_chunks(chunks: List[Any], source_file: str = "") -> None:
    """检查分块后的文档列表。

    Args:
        chunks: split_chunks() 返回的 Document 对象列表
        source_file: 源文件名
    """
    try:
        if not chunks:
            logger.warning("[CHUNKS] No chunks generated!")
            return

        # 提取各块内容长度
        lengths = [len(chunk.page_content) for chunk in chunks]
        avg_len = sum(lengths) / len(lengths) if lengths else 0
        max_len = max(lengths) if lengths else 0
        min_len = min(lengths) if lengths else 0

        # 统计过短的 chunk 数量
        short_chunks = [i for i, l in enumerate(lengths) if l < 50]

        logger.info("=" * 60)
        logger.info("[CHUNKS] 分块结果")
        logger.info("=" * 60)
        logger.info(f"  源文件: {source_file or 'N/A'}")
        logger.info(f"  总块数: {len(chunks)}")
        logger.info(f"  平均块长: {avg_len:.0f} 字符")
        logger.info(f"  最长块: {max_len:,} 字符")
        logger.info(f"  最短块: {min_len:,} 字符")

        if short_chunks:
            logger.warning(f"  ⚠️  存在 {len(short_chunks)} 个过短块 (<50字): {short_chunks[:10]}{'...' if len(short_chunks) > 10 else ''}")

        # 预览前 3 块内容及其元数据
        logger.info("-" * 60)
        logger.info("[CHUNKS] 前3块内容预览:")
        for i, chunk in enumerate(chunks[:3]):
            headings = chunk.metadata.get("headings", [])
            heading_path = " > ".join(headings) if headings else "无标题"
            preview = chunk.page_content[:150].replace("\n", " ")
            logger.info(f"  块 {i+1} [{heading_path}]:")
            logger.info(f"    {preview}...")

        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"[CHUNKS] Inspect failed: {e}")


def inspect_embeddings(vectors: List[List[float]], texts: List[str] = None) -> None:
    """检查 embedding 向量结果。

    Args:
        vectors: embedding 向量列表
        texts: 可选，对应的原始文本列表（供参考）
    """
    try:
        if not vectors:
            logger.warning("[EMBED] No vectors generated!")
            return

        vec_count = len(vectors)
        vec_dim = len(vectors[0]) if vectors else 0

        # 检查零向量
        zero_vectors = []
        for i, vec in enumerate(vectors):
            if all(v == 0 for v in vec):
                zero_vectors.append(i)

        # 检查重复向量
        unique_vectors = len(set(tuple(v) for v in vectors)) if vectors else 0

        logger.info("=" * 60)
        logger.info("[EMBED] 向量化结果")
        logger.info("=" * 60)
        logger.info(f"  向量数量: {vec_count}")
        logger.info(f"  向量维度: {vec_dim}")
        logger.info(f"  独立向量: {unique_vectors}")

        if zero_vectors:
            logger.warning(f"  ⚠️  存在 {len(zero_vectors)} 个全零向量: {zero_vectors[:10]}")

        if vec_count != unique_vectors:
            logger.info(f"  ℹ️  存在重复向量 (可能是相似内容)")

        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"[EMBED] Inspect failed: {e}")


def inspect_retrieval(query: str, results: List[Any], top_k: int = 5) -> None:
    """检查检索结果。

    Args:
        query: 用户的搜索 query
        results: 检索到的 Document 对象列表
        top_k: 展示的最大结果数量
    """
    try:
        if not results:
            logger.warning("[RETRIEVAL] No results returned!")
            return

        logger.info("=" * 60)
        logger.info("[RETRIEVAL] 检索结果")
        logger.info("=" * 60)
        logger.info(f"  查询: {query}")
        logger.info(f"  返回结果数: {len(results)}")

        # 预览 top-k 结果
        display_count = min(top_k, len(results))
        logger.info(f"-" * 60)
        logger.info(f"[RETRIEVAL] Top-{display_count} 结果:")

        for i, doc in enumerate(results[:display_count]):
            score = doc.metadata.get("relevance_score", None)
            headings = doc.metadata.get("headings", [])
            heading_path = " > ".join(headings) if headings else "无标题"

            score_str = f", 相似度: {score:.4f}" if score is not None else ""
            preview = doc.page_content[:120].replace("\n", " ")

            logger.info(f"  结果 {i+1} [{heading_path}]{score_str}:")
            logger.info(f"    {preview}...")

        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"[RETRIEVAL] Inspect failed: {e}")
