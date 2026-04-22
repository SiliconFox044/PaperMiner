"""Embedding 模块 - 基于智谱 AI 的 embedding，支持批量处理与重试。"""

import os
import json
import hashlib
import logging
import traceback
from typing import List, Optional

from langchain_core.embeddings import Embeddings
from tenacity import retry, stop_after_attempt, wait_exponential
import requests

logger = logging.getLogger(__name__)

# 尝试导入 langchain-zhipu，若失败则使用自定义实现
try:
    from langchain_zhipu import ZhipuAIEmbeddings
    HAS_LANGCHAIN_ZHIPU = True
except ImportError:
    HAS_LANGCHAIN_ZHIPU = False


class ZhipuEmbeddings(Embeddings):
    """基于 HTTP API 的智谱 AI Embeddings 自定义实现。"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ZHIPU_API_KEY")
        if not self.api_key:
            raise ValueError("ZHIPU_API_KEY environment variable is not set")
        self.model = "embedding-3"
        self.base_url = "https://open.bigmodel.cn/api/paas/v4/embeddings"

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """使用智谱 API 对文本列表进行 embedding。"""
        embeddings = []
        for text in texts:
            embedding = self._embed_single(text)
            embeddings.append(embedding)
        return embeddings

    def embed_query(self, text: str) -> List[float]:
        """使用智谱 API 对查询文本进行 embedding。"""
        return self._embed_single(text)

    def _embed_single(self, text: str) -> List[float]:
        """对单个文本进行 embedding，支持重试。"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "input": text
        }

        response = requests.post(self.base_url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()

        result = response.json()
        return result["data"][0]["embedding"]


def get_embeddings() -> Embeddings:
    """获取 embeddings 实例，优先使用 langchain-zhipu（若可用）。"""
    if HAS_LANGCHAIN_ZHIPU:
        api_key = os.getenv("ZHIPU_API_KEY")
        return ZhipuAIEmbeddings(model="embedding-3", api_key=api_key)
    else:
        return ZhipuEmbeddings()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4)
)
def embed_batch_with_retry(embeddings: Embeddings, texts: List[str]) -> List[List[float]]:
    """使用指数退避重试对文本批次进行 embedding。

    Args:
        embeddings: Embeddings 实例
        texts: 要 embed 的文本列表

    Returns:
        embedding 向量列表
    """
    return embeddings.embed_documents(texts)


def embed_documents_batch(
    documents: List[str],
    batch_size: int = 50,
    pdf_path: Optional[str] = None,
    data_dir: str = "./data"
) -> List[List[float]]:
    """批量 embedding 文档，支持错误处理。

    Args:
        documents: 要 embed 的文档文本列表
        batch_size: 每批次文档数量（默认 50）
        pdf_path: PDF 文件路径，用于追踪失败的批次
        data_dir: failed_batches.jsonl 所在目录

    Returns:
        所有 embedding 向量的列表

    Raises:
        RuntimeError: 若整批重试后仍然失败
    """
    embeddings = get_embeddings()
    all_embeddings = []
    failed_batches = []

    for i in range(0, len(documents), batch_size):
        batch = documents[i:i + batch_size]
        batch_index = i // batch_size

        try:
            batch_embeddings = embed_batch_with_retry(embeddings, batch)
            all_embeddings.extend(batch_embeddings)
        except Exception as e:
            # 整批失败 — 记录到 failed_batches.jsonl
            failure_record = {
                "pdf_path": pdf_path,
                "batch_index": batch_index,
                "batch_size": len(batch),
                "error": str(e),
                "failed_at": str(__import__("datetime").datetime.now()),
                "document_indices": list(range(i, min(i + batch_size, len(documents))))
            }
            failed_batches.append(failure_record)

            logger.error(f"Batch {batch_index} 详细错误: {traceback.format_exc()}")

            # 写入 failed_batches.jsonl
            failed_batches_path = os.path.join(data_dir, "failed_batches.jsonl")
            with open(failed_batches_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(failure_record, ensure_ascii=False) + "\n")

            raise RuntimeError(
                f"Batch {batch_index} failed after 3 retries. "
                f"PDF: {pdf_path}, Error: {e}. "
                f"See {failed_batches_path} for details."
            )

    return all_embeddings


def get_failed_batches(data_dir: str = "./data") -> List[dict]:
    """从 failed_batches.jsonl 读取之前失败的批次记录。

    Args:
        data_dir: 包含 failed_batches.jsonl 的数据目录

    Returns:
        失败批次记录列表
    """
    failed_batches_path = os.path.join(data_dir, "failed_batches.jsonl")
    if not os.path.exists(failed_batches_path):
        return []

    failed_batches = []
    with open(failed_batches_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                failed_batches.append(json.loads(line))
    return failed_batches


def is_batch_failed(pdf_path: str, batch_index: int, data_dir: str = "./data") -> bool:
    """检查指定批次是否曾失败过。

    Args:
        pdf_path: PDF 文件路径
        batch_index: 批次索引
        data_dir: 数据目录

    Returns:
        若批次被标记为失败则返回 True
    """
    failed_batches = get_failed_batches(data_dir)
    return any(
        fb["pdf_path"] == pdf_path and fb["batch_index"] == batch_index
        for fb in failed_batches
    )


def compute_md5(file_path: str) -> str:
    """计算文件的 MD5 哈希值。

    Args:
        file_path: 文件路径

    Returns:
        MD5 十六进制字符串
    """
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()