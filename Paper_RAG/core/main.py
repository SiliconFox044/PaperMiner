"""Legal RAG 系统的主入口，用于：
1. PDF 上传与处理流水线
2. 带引用标注的问答
3. 基于 MD5 的文档去重
"""

from dotenv import load_dotenv
import os
import json
import threading
import queue
from pathlib import Path

load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'config', '.env'))

from Paper_RAG.pipeline.pdf_parser import parse_pdf
from Paper_RAG.pipeline.text_cleaner import clean_markdown
from Paper_RAG.pipeline.chunk_splitter import split_chunks
from Paper_RAG.pipeline.embedding import embed_documents_batch, compute_md5
from Paper_RAG.registry.md5_records import load_md5_records, save_md5_records
from Paper_RAG.pipeline.vector_store import get_vector_store, add_chunks_to_vector_store, COLLECTION_NAME
from Paper_RAG.retrieval.retrieval import get_retriever
from Paper_RAG.generation.generation import create_generation_chain, _extract_json
from Paper_RAG.registry.paper_registry import load_registry
from Paper_RAG.utils.inspector import (
    logger, save_checkpoint, inspect_parsed, inspect_cleaned,
    inspect_chunks, inspect_embeddings, inspect_retrieval
)


# 路径配置
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data'))

# 懒加载单例：第一次调用时初始化，之后复用
_searcher = None
_generation_chain = None


class PipelineAbortedError(RuntimeError):
    """当处理因并发删除而中止时抛出。"""
    pass


def process_pdf_pipeline(pdf_path: str, file_name: str, should_abort: callable = None) -> str:
    """执行完整的 PDF 处理流水线。

    流水线：PDF → MinerU → 清洗 → 切分 → Embed → Qdrant

    Args:
        pdf_path: 上传的 PDF 文件路径
        file_name: 原始文件名
        should_abort: 当处理必须停止时返回 True 的可调用对象（例如论文正在被删除）

    Returns:
        描述发生了什么的状态消息

    Raises:
        RuntimeError: 处理失败时
        PipelineAbortedError: 检查点处 should_abort 返回 True 时
    """
    # 检查 MD5 去重
    md5_hash = compute_md5(pdf_path)
    records = load_md5_records()

    if md5_hash in records:
        return f"文档已存在（MD5: {md5_hash[:8]}...），已跳过处理。"

    try:
        # 步骤 1：使用 MinerU 解析 PDF
        markdown_text = parse_pdf(pdf_path)
        inspect_parsed(markdown_text, source_file=file_name)
        save_checkpoint("parsed", {"markdown": markdown_text, "source_file": file_name})

        # 步骤 2：清洗 Markdown
        cleaned_text = clean_markdown(markdown_text)
        inspect_cleaned(markdown_text, cleaned_text)
        save_checkpoint("cleaned", {"raw_chars": len(markdown_text), "cleaned_chars": len(cleaned_text), "cleaned_preview": cleaned_text[:1000]})

        # 步骤 3：切分为 chunks
        chunks = split_chunks(cleaned_text)
        inspect_chunks(chunks, source_file=file_name)
        # 保存可序列化的 chunk 检查点数据
        chunk_data = [
            {"page_content": c.page_content, "metadata": c.metadata}
            for c in chunks
        ]
        save_checkpoint("chunks", {"chunks": chunk_data, "total": len(chunks)})

        # Step 4: Add metadata to chunks
        paper_id = md5_hash[:8]  # 从已计算的 md5_hash 截取前8位
        for i, chunk in enumerate(chunks):
            chunk.metadata["paper_id"] = paper_id    # ← 写入 paper_id
            chunk.metadata["source_file"] = file_name
            chunk.metadata["chunk_index"] = i

        # Step 4.5: Save chunks to local file（与旧 batch_processor 格式一致）
        if should_abort and should_abort():
            raise PipelineAbortedError(f"[{paper_id}] chunks 写入前检测到删除请求，已停止")
        local_dir = os.path.join(DATA_DIR, "papers", paper_id)
        os.makedirs(local_dir, exist_ok=True)
        chunks_json_path = os.path.join(local_dir, "chunks.json")
        chunk_data = [
            {"page_content": c.page_content, "metadata": dict(c.metadata)}
            for c in chunks
        ]
        with open(chunks_json_path, "w", encoding="utf-8") as f:
            json.dump(chunk_data, f, ensure_ascii=False, indent=2)

        # 步骤 5：批量 embed chunks
        if should_abort and should_abort():
            raise PipelineAbortedError(f"[{paper_id}] 向量写入前检测到删除请求，已停止")
        texts = [chunk.page_content for chunk in chunks]
        embeddings = embed_documents_batch(
            documents=texts,
            batch_size=50,
            pdf_path=pdf_path,
            data_dir=DATA_DIR
        )
        inspect_embeddings(embeddings, texts)
        # 保存检查点（embeddings 转为列表以便 JSON 序列化）
        save_checkpoint("embeddings", {
            "count": len(embeddings),
            "dimension": len(embeddings[0]) if embeddings else 0,
            "sample": embeddings[0][:5] if embeddings else []
        })

        # 步骤 6：写入 Qdrant
        vector_store = get_vector_store(collection_name=COLLECTION_NAME, data_dir=DATA_DIR)
        add_chunks_to_vector_store(chunks, vector_store, paper_id=paper_id, source_filename=file_name)

        # 步骤 7：Qdrant 写入成功后立即记录 MD5
        # 必须在 Qdrant 写入成功后立即记录，防止崩溃重跑时重复 upsert
        if should_abort and should_abort():
            raise PipelineAbortedError(f"[{paper_id}] md5_records 写入前检测到删除请求，已停止")
        records[md5_hash] = {
            "file_name": file_name,
            "processed_at": str(__import__("datetime").datetime.now()),
            "chunk_count": len(chunks)
        }
        save_md5_records(records)

        return f"成功处理：{file_name}，生成 {len(chunks)} 个 chunks"

    except PipelineAbortedError:
        raise
    except Exception as e:
        logger.error(f"[PIPELINE] 处理失败: {e}")
        raise RuntimeError(f"处理 PDF 时出错: {str(e)}")


def answer_question(
    question: str,
    pre_retrieved_docs: list = None
) -> str:
    """使用 RAG pipeline 回答用户问题。

    Args:
        question: 用户的问题
        pre_retrieved_docs: 可选的预检索文档。若提供，则跳过检索直接使用这些文档；
            若为 None，则正常执行检索。

    Returns:
        带引用的回答
    """
    global _searcher, _generation_chain
    try:
        # [server.py 对接] 支持外部传入预检索文档，用于 paper_ids 范围过滤
        if pre_retrieved_docs is not None:
            docs = pre_retrieved_docs
        else:
            # 懒加载：第一次调用时初始化，之后复用
            if _searcher is None:
                _searcher = get_retriever(
                    collection_name=COLLECTION_NAME,
                    retrieval_mode="demo",
                    data_dir=DATA_DIR
                )
            searcher = _searcher

            # Retrieve + rerank（统一由 searcher 处理）
            try:
                docs = searcher.retrieve_and_rerank(question)
            except Exception as e:
                logger.warning(f"检索或 rerank 失败，降级到纯向量 top-5: {e}")
                docs = searcher.get_retriever().invoke(question)[:5]

            inspect_retrieval(question, docs)
        # [/server.py 对接]

        if not docs:
            return "未找到相关文档，请尝试其他问题或上传相关法律文档。"

        # 懒加载 generation chain
        if _generation_chain is None:
            _generation_chain = create_generation_chain()
        answer = _generation_chain.invoke({"documents": docs, "question": question})

        return _extract_json(answer)

    except Exception as e:
        return f"回答问题时出错: {str(e)}"


def main():
    """程序入口。"""
    import tkinter as tk
    from Paper_RAG.ui import PaperRAGApp

    os.makedirs(DATA_DIR, exist_ok=True)
    root = tk.Tk()
    app = PaperRAGApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
