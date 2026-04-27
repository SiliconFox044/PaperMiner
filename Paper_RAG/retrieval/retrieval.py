"""Retrieval 模块 - 向量检索 + SiliconFlow BGE Reranker for Legal RAG。

Demo 阶段：向量检索 + SiliconFlow BGE Reranker
Full 阶段：向量 + BM25 + EnsembleRetriever（可选，通过 RETRIEVAL_MODE=full 启用）
"""

import os
import logging
import requests
import time
from typing import List, Optional
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny, MinShould

from Paper_RAG.pipeline.vector_store import COLLECTION_NAME, get_qdrant_client, collection_exists
from Paper_RAG.pipeline.embedding import get_embeddings
from Paper_RAG.utils.progress import progress_log as logger

# ── Rerank API 超时配置 ─────────────────────────────────────────────────────
_CONNECT_TIMEOUT = float(os.getenv("RERANK_CONNECT_TIMEOUT", "10"))
_READ_TIMEOUT = float(os.getenv("RERANK_READ_TIMEOUT", "60"))

# ── 日志工具常量 ─────────────────────────────────────────────────────────────
# 阶段名称常量
S_RERANK_API = "rerank_api"
S_VECTOR_SEARCH = "vector_search"
S_RERANK = "rerank"


def _siliconflow_rerank(query: str, docs: List[Document], top_n: int) -> List[Document]:
    """调用硅基流动 API 对文档重排序。

    Args:
        query: 用户查询文本
        docs: 向量检索返回的 Document 列表
        top_n: 返回的文档数量

    Returns:
        经过 rerank 的 top-N Document 列表，metadata 中写入 relevance_score
    """
    t0 = time.perf_counter()
    api_key = os.getenv("SILICONFLOW_API_KEY")
    base_url = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")

    logger(module="retrieval", api="rerank", stage="rerank_api_start", status="start",
           elapsed_ms=0, docs_count=len(docs), top_n=top_n)

    try:
        response = requests.post(
            f"{base_url}/rerank",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "BAAI/bge-reranker-v2-m3",
                "query": query,
                "documents": [doc.page_content for doc in docs],
                "top_n": top_n,
                "return_documents": False
            },
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT)
        )
        http_status = response.status_code
        logger(module="retrieval", api="rerank", stage="rerank_api_response", status="done",
               elapsed_ms=round((time.perf_counter()-t0)*1000,2), http_status=http_status)

        response.raise_for_status()
        results = response.json()["results"]
        results_count = len(results)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger(module="retrieval", api="rerank", stage="rerank_api_done", status="done",
               elapsed_ms=elapsed_ms, results_count=results_count, top_n=top_n)

    except Exception as e:
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        exc_type = type(e).__name__
        exc_msg = str(e)[:80]
        logger(module="retrieval", api="rerank", stage="rerank_api_fail", status="fail",
               elapsed_ms=elapsed_ms, error_type=exc_type, error_msg=exc_msg)
        raise

    # results 是按 relevance_score 降序排列的列表，每项含 index 和 relevance_score
    top_docs = []
    for item in results:
        doc = docs[item["index"]]
        doc.metadata["relevance_score"] = item["relevance_score"]
        top_docs.append(doc)

    return top_docs


class VectorSearchWithReranker:
    """用于 Demo 模式的、基于 SiliconFlow BGE reranking 的向量搜索检索器。"""

    def __init__(
        self,
        collection_name: str = COLLECTION_NAME,
        search_kwargs: Optional[dict] = None,
        reranker_top_n: int = 5,
        data_dir: str = "./data"
    ):
        """初始化带 SiliconFlow BGE reranker 的向量搜索。

        Args:
            collection_name: Qdrant collection 名称
            search_kwargs: 向量检索的搜索参数（默认：{"k": 20}）
            reranker_top_n: rerank 后返回的文档数量
            data_dir: 项目数据根目录（内部自动拼接 qdrant 子目录）
        """
        self.collection_name = collection_name
        self.search_kwargs = search_kwargs or {"k": 20}
        self.reranker_top_n = reranker_top_n
        self.data_dir = data_dir

        # Initialize Qdrant client (join qdrant subdirectory)
        qdrant_path = os.path.join(data_dir, "qdrant")
        self.client = get_qdrant_client(path=qdrant_path)

        # Check if collection exists
        if not collection_exists(self.client, collection_name):
            raise ValueError(f"Collection '{collection_name}' does not exist")

        # Initialize vector store
        self.vector_store = QdrantVectorStore(
            client=self.client,
            collection_name=collection_name,
            embedding=get_embeddings()
        )

    def get_retriever(self) -> BaseRetriever:
        """获取用于向量检索的基础 retriever。"""
        return self.vector_store.as_retriever(
            search_kwargs=self.search_kwargs
        )

    def retrieve_and_rerank(
        self,
        query: str,
        paper_ids: Optional[list[str]] = None,
        rerank_top_n: Optional[int] = None
    ) -> List[Document]:
        """执行向量检索 + SiliconFlow rerank，返回 top-N 文档。

        Args:
            query: 用户查询文本
            paper_ids: 可选，要限制检索的 paper_id 列表
            rerank_top_n: 可选，覆盖默认的 reranker 返回数量（self.reranker_top_n）

        Returns:
            经过 rerank 的 top-N Document 列表
        """
        t0 = time.perf_counter()
        # 构造 Qdrant filter（如果指定了 paper_ids）
        # 兼容两种存储位置：顶层 paper_id 和 metadata.paper_id
        k = self.search_kwargs.get("k", 20)

        logger(module="retrieval", api="retrieve_and_rerank", stage="vector_search_start",
               status="start", elapsed_ms=0, k=k, has_paper_ids=paper_ids is not None)

        if paper_ids:
            qdrant_filter = Filter(
                min_should=MinShould(
                    min_count=1,
                    conditions=[
                        FieldCondition(key="paper_id", match=MatchAny(any=paper_ids)),
                        FieldCondition(key="metadata.paper_id", match=MatchAny(any=paper_ids)),
                    ]
                )
            )
            logger(module="retrieval", api="retrieve_and_rerank", stage="filter_debug",
                   status="info", filter_info=f"paper_ids={paper_ids}")
            docs = self.vector_store.similarity_search(query, k=k, filter=qdrant_filter)
        else:
            docs = self.vector_store.similarity_search(query, k=k)

        vector_elapsed = round((time.perf_counter() - t0) * 1000, 2)
        logger(module="retrieval", api="retrieve_and_rerank", stage="vector_search_done",
               status="done", elapsed_ms=vector_elapsed, docs_count=len(docs))

        if not docs:
            return []

        effective_top_n = rerank_top_n if rerank_top_n is not None else self.reranker_top_n
        t_rerank = time.perf_counter()
        logger(module="retrieval", api="retrieve_and_rerank", stage="rerank_start",
               status="start", elapsed_ms=0, reranker_top_n=effective_top_n)

        top_docs = _siliconflow_rerank(query, docs, effective_top_n)

        rerank_elapsed = round((time.perf_counter() - t_rerank) * 1000, 2)
        logger(module="retrieval", api="retrieve_and_rerank", stage="rerank_done",
               status="done", elapsed_ms=rerank_elapsed, top_docs_count=len(top_docs))

        return top_docs


# BM25 和 EnsembleRetriever 保留给 Full 版本（RETRIEVAL_MODE=full）
# 代码路径已保留但默认不在 Demo 阶段启用

RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "demo")


def get_retriever(
    collection_name: str = COLLECTION_NAME,
    retrieval_mode: str = RETRIEVAL_MODE,
    data_dir: str = "./data"
) -> BaseRetriever:
    """根据检索模式获取对应 retriever 的工厂函数。

    Args:
        collection_name: Qdrant collection 名称
        retrieval_mode: 'demo'（向量 + SiliconFlow reranker）或 'full'（向量 + BM25 + SiliconFlow reranker）
        data_dir: 项目数据根目录（内部自动拼接 qdrant 子目录）

    Returns:
        配置好的 retriever 实例
    """
    if retrieval_mode == "demo":
        # Demo 模式：纯向量检索 + SiliconFlow BGE Reranker
        # 返回 VectorSearchWithReranker 实例，供调用方使用 retrieve_and_rerank()
        vs_with_reranker = VectorSearchWithReranker(
            collection_name=collection_name,
            search_kwargs={"k": 20},
            reranker_top_n=5,
            data_dir=data_dir
        )
        return vs_with_reranker
    elif retrieval_mode == "full":
        # Full 模式：向量 + BM25 + EnsembleRetriever + SiliconFlow BGE Reranker
        # 注意：BM25 实现需要额外配置
        raise NotImplementedError(
            "Full mode (RETRIEVAL_MODE=full) is reserved for after ablation study. "
            "Use demo mode for now."
        )
    else:
        raise ValueError(f"Invalid RETRIEVAL_MODE: {retrieval_mode}. Use 'demo' or 'full'.")
