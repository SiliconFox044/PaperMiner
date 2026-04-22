"""Vector Store 模块 - Legal RAG 的 Qdrant 集成。"""

import os
from typing import List, Optional

from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http import models

from Paper_RAG.pipeline.embedding import get_embeddings, embed_documents_batch


COLLECTION_NAME = "law_papers"
VECTOR_SIZE = 2048  # 智谱 embedding-3 向量维度

# ── 单例 client ───────────────────────────────────────────────────────────────

_qdrant_client: QdrantClient | None = None
_qdrant_client_path: str | None = None


def get_qdrant_client(path: str = "./data/qdrant") -> QdrantClient:
    """获取或创建单例 QdrantClient 实例。

    所有调用方在进程生命周期内共享同一个 client 实例。
    首个调用方决定路径；后续调用方必须使用相同路径，否则会共享该实例。

    Args:
        path: Qdrant 存储目录路径

    Returns:
        共享的 QdrantClient 实例
    """
    global _qdrant_client, _qdrant_client_path
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(path=path)
        _qdrant_client_path = path
    return _qdrant_client


def collection_exists(client: QdrantClient, collection_name: str) -> bool:
    """检查 collection 是否存在。

    Args:
        client: QdrantClient 实例
        collection_name: collection 名称

    Returns:
        若 collection 存在则返回 True
    """
    try:
        client.get_collection(collection_name)
        return True
    except (UnexpectedResponse, Exception):
        return False


def create_collection_if_not_exists(client: QdrantClient, collection_name: str = COLLECTION_NAME):
    """若 collection 不存在则创建。

    使用与智谱 embedding-3 模型相同的 embedding 维度。

    Args:
        client: QdrantClient 实例
        collection_name: 要创建的 collection 名称
    """
    if not collection_exists(client, collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=VECTOR_SIZE,
                distance=models.Distance.COSINE
            )
        )


def get_vector_store(
    embedding_batch_size: int = 50,
    collection_name: str = COLLECTION_NAME,
    data_dir: str = "./data"
) -> QdrantVectorStore:
    """获取或创建 QdrantVectorStore 实例。

    Args:
        embedding_batch_size: embeddings 批处理大小（传递给 embedding 模块）
        collection_name: collection 名称
        data_dir: 数据目录路径

    Returns:
        可用于增量插入的 QdrantVectorStore 实例
    """
    client = get_qdrant_client(path=os.path.join(data_dir, "qdrant"))
    create_collection_if_not_exists(client, collection_name)

    embeddings = get_embeddings()

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        embedding=embeddings
    )

    return vector_store


def add_chunks_to_vector_store(
    documents: List[Document],
    vector_store: Optional[QdrantVectorStore] = None,
    collection_name: str = COLLECTION_NAME,
    data_dir: str = "./data",
    paper_id: str = "",
    source_filename: str = ""
) -> int:
    """将文档 chunk 添加到 Qdrant 向量库。

    Args:
        documents: 要添加的 Document 对象列表
        vector_store: 已有的 QdrantVectorStore 实例（可选）
        collection_name: collection 名称
        data_dir: 数据目录路径
        paper_id: 要存在 payload 中的论文标识符（MD5 前缀）
        source_filename: 要存在 payload 中的原始 PDF 文件名

    Returns:
        添加的文档数量
    """
    if vector_store is None:
        vector_store = get_vector_store(collection_name=collection_name, data_dir=data_dir)

    # 存储前为每个文档的 metadata 添加标注
    for doc in documents:
        doc.metadata["paper_id"] = paper_id
        doc.metadata["source_file"] = source_filename  # 与 server.py 读取字段名保持一致

    # 添加文档（QdrantVectorStore.add_documents 内部处理批处理）
    vector_store.add_documents(documents)

    return len(documents)


def embed_and_store(
    chunks: List[str],
    metadatas: List[dict] = None,
    collection_name: str = COLLECTION_NAME,
    data_dir: str = "./data",
    batch_size: int = 50,
    pdf_path: str = None,
    paper_id: str = "",
    source_filename: str = ""
) -> dict:
    """对 chunk 进行 embedding 并存入 Qdrant 向量库。

    将 embedding 生成和 Qdrant 存储合并为一次调用。

    Args:
        chunks: 要 embed 并存储的文本 chunk 列表
        metadatas: 与 chunks 对齐的 metadata 字典列表（可选）
        collection_name: Qdrant collection 名称
        data_dir: 数据目录路径
        batch_size: embedding 生成的批处理大小
        pdf_path: 可选的 PDF 路径，用于追踪失败的批次
        paper_id: 要存在 payload 中的论文标识符（MD5 前缀）
        source_filename: 原始 PDF 文件名

    Returns:
        诊断信息字典，包含键：
            total_chunks: 输入 chunk 总数
            vectors_generated: 成功生成的向量数
            vectors_stored: 写入 Qdrant 的向量数
            vector_dim: 每个向量的维度
            collection_name: 写入的 collection 名称
            failed_batches: failed_batches.jsonl 中的失败批次数量
    """
    if metadatas is None:
        metadatas = [{} for _ in chunks]

    # 步骤 1：生成 embeddings
    vectors = embed_documents_batch(
        documents=chunks,
        batch_size=batch_size,
        pdf_path=pdf_path,
        data_dir=data_dir
    )

    vectors_generated = len(vectors)
    vector_dim = len(vectors[0]) if vectors else 0

    # 步骤 2：构建 Document 对象
    documents = []
    for chunk_text, metadata in zip(chunks, metadatas):
        doc = Document(page_content=chunk_text, metadata=metadata)
        documents.append(doc)

    # 步骤 3：存入 Qdrant — upsert 预生成向量，避免重复 embed
    import uuid
    from qdrant_client.http import models as qdrant_models

    client = get_qdrant_client(path=os.path.join(data_dir, "qdrant"))
    create_collection_if_not_exists(client, collection_name)

    points = []
    for doc, vector in zip(documents, vectors):
        points.append(
            qdrant_models.PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    **doc.metadata,
                    "page_content": doc.page_content,
                    "paper_id": paper_id,
                    "source_file": source_filename,  # 与 server.py 读取字段名保持一致
                }
            )
        )

    client.upsert(collection_name=collection_name, points=points)
    vectors_stored = len(points)

    # 步骤 4：读取失败批次数量
    failed_batches_path = os.path.join(data_dir, "failed_batches.jsonl")
    failed_batches = 0
    if os.path.exists(failed_batches_path):
        with open(failed_batches_path, "r", encoding="utf-8") as f:
            failed_batches = sum(1 for line in f if line.strip())

    return {
        "total_chunks": len(chunks),
        "vectors_generated": vectors_generated,
        "vectors_stored": vectors_stored,
        "vector_dim": vector_dim,
        "collection_name": collection_name,
        "failed_batches": failed_batches
    }


def count_paper_vectors(
    paper_id: str,
    collection_name: str = COLLECTION_NAME,
    data_dir: str = "./data"
) -> int:
    """统计 Qdrant 中属于指定论文的向量数量。

    Args:
        paper_id: 要统计向量数量的论文标识符。
        collection_name: Qdrant collection 名称。
        data_dir: 数据目录路径。

    Returns:
        找到的向量数量，若 collection 不存在则返回 0。
    """
    client = get_qdrant_client(path=os.path.join(data_dir, "qdrant"))
    if not collection_exists(client, collection_name):
        return 0

    def _count(key: str) -> int:
        result = client.count(
            collection_name=collection_name,
            count_filter=models.Filter(
                must=[models.FieldCondition(
                    key=key,
                    match=models.MatchValue(value=paper_id)
                )]
            ),
            exact=True
        )
        return result.count

    # 兼容新格式（顶层）和旧格式（metadata 子字段）
    return _count("paper_id") + _count("metadata.paper_id")


def delete_paper_vectors(
    paper_id: str,
    collection_name: str = COLLECTION_NAME,
    data_dir: str = "./data"
) -> int:
    """从 Qdrant 中删除属于指定论文的所有向量。

    Args:
        paper_id: 要过滤的论文标识符（MD5 前缀）。
        collection_name: Qdrant collection 名称。
        data_dir: 数据目录路径（内部自动拼接 Qdrant 存储子目录）。

    Returns:
        删除的向量数量，若无法确定数量则返回 -1。
    """
    client = get_qdrant_client(path=os.path.join(data_dir, "qdrant"))

    def _delete(key: str):
        client.delete(
            collection_name=collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(
                        key=key,
                        match=models.MatchValue(value=paper_id)
                    )]
                )
            )
        )

    # 同时删除新格式和旧格式
    _delete("paper_id")
    _delete("metadata.paper_id")
    return 0