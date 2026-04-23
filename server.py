"""LegalRAG HTTP API Server.

独立 Web 服务入口，不修改 Paper_RAG/ 内部任何现有文件，
只从外部导入并调用其中的模块。

启动命令：python server.py 或 uvicorn server:app --reload --port 8000
"""

import json
import logging
import sys
import os
import shutil
import tempfile
import threading
import uuid
from hashlib import md5
from pathlib import Path
from time import perf_counter
from typing import Optional

os.environ['NO_PROXY'] = '*' 

# ── 路径初始化（必须优先）─────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

# ── 环境变量（加载 Paper_RAG/.env）────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "Paper_RAG", "config", ".env"))

# ── FastAPI 框架────────────────────────────────────────────────────────────────
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ── Paper_RAG 核心模块导入───────────────────────────────────────────────────────
from Paper_RAG.core.main import answer_question, get_retriever, COLLECTION_NAME, DATA_DIR
from Paper_RAG.registry.paper_registry import (
    load_registry,
    save_registry,
    get_folder_tree,
    create_folder,
    rename_folder,
    delete_folder,
    move_paper_to_folder,
    update_paper_status,
    delete_single_paper,
)
from Paper_RAG.pipeline.vector_store import get_vector_store, add_chunks_to_vector_store
from Paper_RAG.pipeline.pdf_parser import parse_pdf
from Paper_RAG.pipeline.text_cleaner import clean_markdown
from Paper_RAG.pipeline.chunk_splitter import split_chunks
from Paper_RAG.pipeline.embedding import embed_documents_batch, compute_md5
from Paper_RAG.utils.progress import progress_log

# ── 项目路径常量────────────────────────────────────────────────────────────────
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))

# ── 日志配置────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 日志工具常量 ───────────────────────────────────────────────────────────────
LOG_PREFIX = "PROGRESS"

# 阶段名称常量
S_REQUEST_START = "request_start"
S_REQUEST_DONE = "request_done"
S_RETRIEVE = "retrieve"
S_RETRIEVE_START = "retrieve_start"
S_RETRIEVE_DONE = "retrieve_done"
S_LLM_ANALYSIS = "llm_analysis"
S_LLM_ANALYSIS_START = "llm_analysis_start"
S_LLM_ANALYSIS_DONE = "llm_analysis_done"
S_JSON_PARSE = "json_parse"
S_JSON_PARSE_DONE = "json_parse_done"
S_JSON_PARSE_FAIL = "json_parse_fail"
S_GENERATE = "generate"
S_GENERATE_START = "generate_start"
S_GENERATE_DONE = "generate_done"
S_QA_GENERATE = "qa_generate"
S_QA_GENERATE_START = "qa_generate_start"
S_QA_GENERATE_DONE = "qa_generate_done"
S_OPINION_GENERATE = "opinion_generate"
S_OPINION_GENERATE_START = "opinion_generate_start"
S_OPINION_GENERATE_DONE = "opinion_generate_done"
S_JSON_PARSE_START = "json_parse_start"
S_EMPTY_RETURN = "empty_return"
S_NO_QUALITY_DOCS = "no_quality_docs"

# ── 日志辅助函数 ──────────────────────────────────────────────────────────────


def _gen_request_id() -> str:
    """生成唯一的 request_id"""
    return uuid.uuid4().hex[:16]


def _log(module: str, api: str, request_id: str, stage: str, status: str,
        elapsed_ms: float = 0, **kwargs):
    """统一日志输出"""
    base = {
        "module": module,
        "api": api,
        "request_id": request_id,
        "stage": stage,
        "status": status,
        "elapsed_ms": round(elapsed_ms, 2) if elapsed_ms else 0,
    }
    base.update(kwargs)
    progress_log(**base)


def _exc_summary(e: Exception, max_len: int = 80) -> str:
    """将异常转为短摘要文本"""
    msg = f"{type(e).__name__}: {str(e)}"
    return msg[:max_len] if len(msg) > max_len else msg

# ── FastAPI 应用初始化──────────────────────────────────────────────────────────
app = FastAPI(title="LegalRAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 启动指纹日志 ────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def _boot_log():
    import platform
    import datetime
    progress_log(
        module="boot",
        api="server",
        stage="server_start",
        status="start",
        elapsed_ms=0,
        pid=os.getpid(),
        hostname=platform.node(),
        timestamp=datetime.datetime.now().isoformat(),
        python_version=platform.python_version(),
    )

    # ── 孤儿任务扫描：服务异常中断时将 processing 状态置为 failed ───────────────
    registry = load_registry()
    changed = False
    for paper_id, paper in registry["papers"].items():
        if paper.get("status") == "processing":
            paper["status"] = "failed"
            paper["error_msg"] = "服务重启，任务中断"
            changed = True
    if changed:
        save_registry(registry)
        logger.info("[startup] 已将孤儿任务（processing）全部标记为 failed")


# ── 检索历史持久化 ───────────────────────────────────────────────────────────────
HISTORY_DIR = Path("data/history")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/history/opinion")
def get_opinion_history():
    path = HISTORY_DIR / "opinion.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/history/opinion")
def save_opinion_history(body: list = Body(...)):
    path = HISTORY_DIR / "opinion.json"
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


@app.get("/api/history/qa")
def get_qa_history():
    path = HISTORY_DIR / "qa.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/history/qa")
def save_qa_history(body: list = Body(...)):
    path = HISTORY_DIR / "qa.json"
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


@app.get("/api/history/qa/current")
def get_qa_current():
    path = HISTORY_DIR / "qa_current.json"
    if not path.exists():
        return {"id": None}
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/history/qa/current")
def save_qa_current(body: dict = Body(...)):
    path = HISTORY_DIR / "qa_current.json"
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


# ── 全局单例初始化──────────────────────────────────────────────────────────────
_searcher = None
_qa_chain = None


def get_searcher():
    """返回检索器全局单例，延迟初始化（与 main.py 方式一致）。"""
    global _searcher
    if _searcher is None:
        _searcher = get_retriever(
            collection_name=COLLECTION_NAME,
            retrieval_mode="demo",
            data_dir=DATA_DIR,
        )
    return _searcher


def get_qa_chain():
    """返回 QA 生成链全局单例，延迟初始化。"""
    global _qa_chain
    if _qa_chain is None:
        from Paper_RAG.generation.generation import create_qa_chain
        _qa_chain = create_qa_chain()
    return _qa_chain


# ── Pydantic 请求/响应模型──────────────────────────────────────────────────────

class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, description="搜索文本")
    top_k: int = Field(default=5, ge=3, le=20, description="返回结果数量")
    paper_ids: list[str] = Field(default_factory=list, description="指定检索范围的文档 ID 列表")


class RetrieveResultItem(BaseModel):
    id: str
    source: str
    similarity: float
    text: str
    path: str


class RetrieveResponse(BaseModel):
    results: list[RetrieveResultItem]
    analysis: Optional[str] = None
    analysis_sources: Optional[list[dict]] = None


class AnswerRequest(BaseModel):
    question: str = Field(..., min_length=1, description="问题文本")
    paper_ids: list[str] = Field(default_factory=list, description="指定检索范围的文档文件名列表")
    mode: str = Field(default="qa", description="生成模式：qa=知识问答，opinion=观点搜索引用")


class SourceItem(BaseModel):
    file: str
    path: str
    excerpt: str


class AnswerResponse(BaseModel):
    answer: str
    sources: list[SourceItem]


class HealthResponse(BaseModel):
    status: str


class DocumentRecord(BaseModel):
    id: str
    title: str
    addedDate: str
    status: str
    folder_id: str


class FolderNode(BaseModel):
    id: str
    name: str
    parent_id: Optional[str] = None
    is_system: bool = False
    children: Optional[list["FolderNode"]] = None


class DocumentLibraryResponse(BaseModel):
    folders: list[FolderNode]
    documents: list[DocumentRecord]


class UploadResponse(BaseModel):
    status: str
    paper_id: str


class CreateFolderRequest(BaseModel):
    name: str
    parent_id: Optional[str] = None


class RenameFolderRequest(BaseModel):
    name: str


class MovePaperRequest(BaseModel):
    folder_id: str


class DeleteFolderResponse(BaseModel):
    deleted_folders: list[str]
    moved_papers: list[str]


class DeleteDocumentResponse(BaseModel):
    status: str  # "deleted" | "failed" | "partial"
    delete_status: str  # "completed" | "partial" | "failed"
    detail: str
    paper_id: str | None = None
    filename: str | None = None
    vectors_before: int | None = None
    vectors_after: int | None = None
    local_deleted: bool | None = None


# ── 辅助函数─────────────────────────────────────────────────────────────────────

def _doc_to_result_item(doc) -> RetrieveResultItem:
    """将 LangChain Document 转换为 API 响应模型。"""
    headings = doc.metadata.get("headings", [])
    if isinstance(headings, list):
        path_str = " > ".join(headings)
    else:
        path_str = str(headings)
    doc_id = md5(doc.page_content[:100].encode()).hexdigest()[:16]
    similarity = float(doc.metadata.get("relevance_score", 0.0))
    return RetrieveResultItem(
        id=doc_id,
        source=doc.metadata.get("source_file", "未知来源"),
        similarity=round(similarity, 4),
        text=doc.page_content,
        path=path_str,
    )


# ── 接口实现────────────────────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """健康检查，确认服务在线。"""
    return HealthResponse(status="ok")


@app.post("/api/retrieve", response_model=RetrieveResponse)
async def retrieve(request: RetrieveRequest):
    """观点搜索：向量检索 + Jina rerank + LLM 分析。

    Args:
        request.query: 搜索文本
        request.top_k: 返回数量（范围 3-20，默认 5）

    Returns:
        results: 检索结果列表，每项含 id、source、similarity、text、path
        analysis: LLM 对观点与片段关系的分析结论（opinion 模式）
        analysis_sources: LLM 引用的证据片段列表
    """
    if not request.query.strip():
        raise HTTPException(status_code=422, detail="query 不能为空")

    request_id = _gen_request_id()
    t0 = perf_counter()
    query_len = len(request.query)
    paper_ids_count = len(request.paper_ids) if request.paper_ids else 0
    _log("2", "/api/retrieve", request_id, S_REQUEST_START, "start",
         elapsed_ms=0, query_len=query_len, top_k=request.top_k, paper_ids_count=paper_ids_count)

    try:
        searcher = get_searcher()
        used_fallback = False

        # ── 检索阶段 ───────────────────────────────────────────────────────────
        _log("2", "/api/retrieve", request_id, S_RETRIEVE_START, "start", elapsed_ms=0)
        t_retrieve = perf_counter()
        try:
            docs = searcher.retrieve_and_rerank(
                request.query,
                request.paper_ids if request.paper_ids else None,
                rerank_top_n=request.top_k
            )
        except Exception as rerank_err:
            used_fallback = True
            fallback_reason = _exc_summary(rerank_err)
            logger.warning(f"Reranker 失败，降级到纯向量检索: {rerank_err}")
            docs = searcher.get_retriever().invoke(request.query)
            docs = docs[:request.top_k]

        docs_count = len(docs)
        retrieve_elapsed = (perf_counter() - t_retrieve) * 1000
        _log("2", "/api/retrieve", request_id, S_RETRIEVE_DONE, "done",
             elapsed_ms=retrieve_elapsed, docs_count=docs_count,
             used_fallback=used_fallback if "used_fallback" in dir() else False,
             fallback_reason=fallback_reason if used_fallback else None)

        results = [_doc_to_result_item(doc) for doc in docs[: request.top_k]]

        # ── 相似度过滤：只取 similarity > 0.7 的片段发给 LLM ─────────────────
        SIMILARITY_THRESHOLD = 0.7
        high_quality_docs = [
            doc for doc in docs
            if doc.metadata.get("relevance_score", 0.0) > SIMILARITY_THRESHOLD
        ]
        docs_for_llm = high_quality_docs[:5] if len(high_quality_docs) > 5 else high_quality_docs

        # ── LLM 分析阶段 ───────────────────────────────────────────────────────
        _log("2", "/api/retrieve", request_id, S_LLM_ANALYSIS_START, "start", elapsed_ms=0)
        analysis: Optional[str] = None
        analysis_sources: Optional[list[dict]] = None
        t_llm = perf_counter()
        raw_analysis = None
        llm_status = "done"
        try:
            if not docs_for_llm:
                # 无合格片段，返回固定文本
                llm_status = "no_quality_docs"
                analysis = (
                    "⚪ 缺乏支撑\n\n"
                    "目前文献库中缺乏与该论点相似的文献片段。"
                    "接下来展示的文献片段与原论点相似系数<0.6，仅供参考。"
                )
                _log("2", "/api/retrieve", request_id, S_NO_QUALITY_DOCS, "done",
                     elapsed_ms=0, analysis_len=len(analysis))
            else:
                raw_analysis = answer_question(
                    question=request.query,
                    pre_retrieved_docs=docs_for_llm
                )
                llm_elapsed = (perf_counter() - t_llm) * 1000
                if not raw_analysis or not raw_analysis.strip():
                    llm_status = "empty"
                    logger.warning("[/api/retrieve] LLM 返回空内容，跳过解析")
                    _log("2", "/api/retrieve", request_id, S_LLM_ANALYSIS_DONE, "empty",
                         elapsed_ms=llm_elapsed, analysis_len=0)
                else:
                    _log("2", "/api/retrieve", request_id, S_JSON_PARSE_START, "start", elapsed_ms=0)
                    t_parse = perf_counter()
                    try:
                        parsed = json.loads(raw_analysis)
                        analysis = parsed.get("answer", None)
                        analysis_sources = parsed.get("sources", None)
                        parse_elapsed = (perf_counter() - t_parse) * 1000
                        _log("2", "/api/retrieve", request_id, S_JSON_PARSE_DONE, "done",
                             elapsed_ms=parse_elapsed,
                             has_analysis=bool(analysis),
                             analysis_sources_count=len(analysis_sources) if analysis_sources else 0)
                        _log("2", "/api/retrieve", request_id, S_LLM_ANALYSIS_DONE, "done",
                             elapsed_ms=llm_elapsed, analysis_len=len(raw_analysis))
                    except json.JSONDecodeError as je:
                        parse_elapsed = (perf_counter() - t_parse) * 1000
                        llm_status = "parse_fail"
                        logger.warning(f"[/api/retrieve] LLM 返回非 JSON 结果，跳过解析: {je}")
                        _log("2", "/api/retrieve", request_id, S_JSON_PARSE_FAIL, "fail",
                             elapsed_ms=parse_elapsed, error_type="JSONDecodeError",
                             error_msg=_exc_summary(je))
        except Exception as e:
            llm_elapsed = (perf_counter() - t_llm) * 1000
            llm_status = "fail"
            logger.warning(f"[/api/retrieve] LLM 分析调用失败，继续返回检索结果: {e}")
            _log("2", "/api/retrieve", request_id, S_LLM_ANALYSIS_DONE, "fail",
                 elapsed_ms=llm_elapsed, error_type=type(e).__name__,
                 error_msg=_exc_summary(e))

        total_elapsed = (perf_counter() - t0) * 1000
        _log("2", "/api/retrieve", request_id, S_REQUEST_DONE, "done",
             elapsed_ms=total_elapsed,
             results_count=len(results),
             has_analysis=bool(analysis),
             analysis_sources_count=len(analysis_sources) if analysis_sources else 0)

        return RetrieveResponse(
            results=results,
            analysis=analysis,
            analysis_sources=analysis_sources,
        )
    except Exception as e:
        total_elapsed = (perf_counter() - t0) * 1000
        logger.exception(f"[/api/retrieve] 检索异常: {e}")
        _log("2", "/api/retrieve", request_id, S_REQUEST_DONE, "fail",
             elapsed_ms=total_elapsed, error_type=type(e).__name__,
             error_msg=_exc_summary(e))
        raise HTTPException(status_code=500, detail=f"检索失败: {str(e)}")


@app.post("/api/answer", response_model=AnswerResponse)
async def answer(request: AnswerRequest):
    """知识问答/观点搜索引用：基于检索结果生成结构化分析。

    Args:
        request.question: 问题文本
        request.paper_ids: 可选，指定检索范围的文档文件名列表
        request.mode: "qa"=知识问答（使用 QA_PROMPT），"opinion"=观点搜索引用（使用 ANALYST_PROMPT）

    Returns:
        answer: 分析文本
        sources: 参考文档片段列表
    """
    if not request.question.strip():
        raise HTTPException(status_code=422, detail="question 不能为空")

    request_id = _gen_request_id()
    t0 = perf_counter()
    question_len = len(request.question)
    paper_ids_count = len(request.paper_ids) if request.paper_ids else 0
    mode = request.mode
    _log("3", "/api/answer", request_id, S_REQUEST_START, "start",
         elapsed_ms=0, mode=mode, question_len=question_len, paper_ids_count=paper_ids_count)

    try:
        searcher = get_searcher()
        used_fallback = False

        # ── 检索文档 ────────────────────────────────────────────────────────
        _log("3", "/api/answer", request_id, S_RETRIEVE_START, "start", elapsed_ms=0)
        t_retrieve = perf_counter()
        if not request.paper_ids:
            try:
                docs = searcher.retrieve_and_rerank(request.question)
            except Exception as rerank_err:
                used_fallback = True
                fallback_reason = _exc_summary(rerank_err)
                logger.warning(f"Reranker 失败，降级到纯向量检索: {rerank_err}")
                docs = searcher.get_retriever().invoke(request.question)
        else:
            try:
                docs = searcher.retrieve_and_rerank(request.question, request.paper_ids)
            except Exception as rerank_err:
                used_fallback = True
                fallback_reason = _exc_summary(rerank_err)
                logger.warning(f"Reranker 失败，降级到纯向量检索: {rerank_err}")
                all_docs = searcher.get_retriever().invoke(request.question)
                docs = [
                    doc for doc in all_docs
                    if doc.metadata.get("paper_id") in request.paper_ids
                    or doc.metadata.get("metadata.paper_id") in request.paper_ids
                ]

        docs_count = len(docs)
        retrieve_elapsed = (perf_counter() - t_retrieve) * 1000
        _log("3", "/api/answer", request_id, S_RETRIEVE_DONE, "done",
             elapsed_ms=retrieve_elapsed, docs_count=docs_count,
             used_fallback=used_fallback, fallback_reason=fallback_reason if used_fallback else None)

        if not docs:
            total_elapsed = (perf_counter() - t0) * 1000
            _log("3", "/api/answer", request_id, S_EMPTY_RETURN, "done",
                 elapsed_ms=total_elapsed, reason="no_docs")
            _log("3", "/api/answer", request_id, S_REQUEST_DONE, "done",
                 elapsed_ms=total_elapsed, answer_len=0, sources_count=0)
            return AnswerResponse(
                answer="当前文献库中缺乏回答该问题的相关内容。",
                sources=[],
            )

        # ── 生成答案 ────────────────────────────────────────────────────────
        generate_stage_start = S_QA_GENERATE_START if request.mode == "qa" else S_OPINION_GENERATE_START
        generate_stage_done = S_QA_GENERATE_DONE if request.mode == "qa" else S_OPINION_GENERATE_DONE
        _log("3", "/api/answer", request_id, generate_stage_start, "start", elapsed_ms=0)
        t_generate = perf_counter()
        raw_answer = None
        if request.mode == "qa":
            # QA 模式：使用 QA 生成链，不经过 answer_question
            chain = get_qa_chain()
            raw_answer = chain.invoke({"documents": docs, "question": request.question})
        else:
            # Opinion 模式：使用现有的 answer_question
            raw_answer = answer_question(request.question, pre_retrieved_docs=docs)

        generate_elapsed = (perf_counter() - t_generate) * 1000
        _log("3", "/api/answer", request_id, generate_stage_done, "done",
             elapsed_ms=generate_elapsed, raw_answer_len=len(raw_answer) if raw_answer else 0)

        # ── 解析 LLM 返回的 JSON ────────────────────────────────────────────
        _log("3", "/api/answer", request_id, S_JSON_PARSE_START, "start", elapsed_ms=0)
        t_parse = perf_counter()
        answer_text = raw_answer
        sources: list[SourceItem] = []
        try:
            parsed = json.loads(raw_answer)
            answer_text = parsed.get("answer", raw_answer)
            sources = [
                SourceItem(
                    file=s.get("file", ""),
                    path=s.get("path", ""),
                    excerpt=s.get("excerpt", ""),
                )
                for s in parsed.get("sources", [])
            ]
            parse_elapsed = (perf_counter() - t_parse) * 1000
            _log("3", "/api/answer", request_id, S_JSON_PARSE_DONE, "done",
                 elapsed_ms=parse_elapsed,
                 answer_len=len(answer_text),
                 sources_count=len(sources))
        except json.JSONDecodeError as je:
            parse_elapsed = (perf_counter() - t_parse) * 1000
            logger.warning(f"[/api/answer] JSON 解析失败，原始输出：{raw_answer[:500]}")
            _log("3", "/api/answer", request_id, S_JSON_PARSE_FAIL, "fail",
                 elapsed_ms=parse_elapsed, error_type="JSONDecodeError",
                 error_msg=_exc_summary(je))
            answer_text = raw_answer
            sources = []

        total_elapsed = (perf_counter() - t0) * 1000
        _log("3", "/api/answer", request_id, S_REQUEST_DONE, "done",
             elapsed_ms=total_elapsed,
             answer_len=len(answer_text),
             sources_count=len(sources))

        return AnswerResponse(answer=answer_text, sources=sources)

    except Exception as e:
        total_elapsed = (perf_counter() - t0) * 1000
        logger.exception(f"[/api/answer] 生成异常: {e}")
        _log("3", "/api/answer", request_id, S_REQUEST_DONE, "fail",
             elapsed_ms=total_elapsed, error_type=type(e).__name__,
             error_msg=_exc_summary(e))
        raise HTTPException(status_code=500, detail=f"生成失败: {str(e)}")


# ── 文档库接口 ────────────────────────────────────────────────────────────────

@app.get("/api/documents", response_model=DocumentLibraryResponse)
async def list_documents():
    """获取文档库列表：文件夹树和文档记录。

    Returns:
        folders: 文件夹层级结构（来自 get_folder_tree）
        documents: 文档记录列表（含 id、title、addedDate、status、folder_id）
    """
    try:
        registry = load_registry()
        folders = get_folder_tree()

        documents = []
        papers = registry.get("papers", registry)
        for paper_id, info in papers.items():
            if info.get("status") in ("deleted",):
                continue
            reg_status = info.get("status", "unknown")
            # 状态映射：registry 状态 → 前端状态
            if reg_status == "completed":
                status = "ready"
            elif reg_status in ("pending", "processing"):
                status = "processing"
            elif reg_status == "failed":
                status = "failed"
            else:
                status = reg_status

            # 时间格式化为 YYYY-MM-DD
            registered_at = info.get("registered_at", "")
            if registered_at:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(registered_at)
                    added_date = dt.strftime("%Y-%m-%d")
                except Exception:
                    added_date = registered_at[:10] if len(registered_at) >= 10 else registered_at
            else:
                added_date = ""

            # 去掉 .pdf 后缀作为标题
            title = info.get("original_filename", "未知文件")
            if title.lower().endswith(".pdf"):
                title = title[:-4]

            documents.append(
                DocumentRecord(
                    id=paper_id,
                    title=title,
                    addedDate=added_date,
                    status=status,
                    folder_id=info.get("folder_id", "uncategorized"),
                )
            )

        return DocumentLibraryResponse(folders=folders, documents=documents)

    except Exception as e:
        logger.exception(f"[/api/documents] 获取文档列表异常: {e}")
        raise HTTPException(status_code=500, detail=f"获取文档列表失败: {str(e)}")


@app.post("/api/upload", response_model=UploadResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """上传 PDF 文件并异步处理。

    Args:
        file: 上传的 PDF 文件（multipart/form-data，字段名 file）

    Returns:
        status: "processing"
        paper_id: 预分配的 paper_id
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="仅支持 PDF 文件")

    try:
        # 写入临时文件
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp_path = tmp.name
        content = await file.read()
        tmp.write(content)
        tmp.close()

        # 计算 paper_id（MD5 前8位）
        paper_id = compute_md5(tmp_path)[:8]

        # 持久化源 PDF（独立于流水线，失败重试时使用）
        source_pdf_path = os.path.join("data", "papers", paper_id, "source.pdf")
        os.makedirs(os.path.dirname(source_pdf_path), exist_ok=True)
        shutil.copy2(tmp_path, source_pdf_path)

        # 预注册到 registry
        from Paper_RAG.registry.paper_registry import load_registry, save_registry
        registry = load_registry()
        registry["papers"][paper_id] = {
            "paper_id": paper_id,
            "original_filename": file.filename,
            "pdf_path": source_pdf_path,
            "local_dir": f"data/papers/{paper_id}/",
            "status": "processing",
            "chunk_count": 0,
            "registered_at": __import__("datetime").datetime.utcnow().isoformat(),
            "completed_at": None,
            "error_msg": None,
            "folder_id": "uncategorized",
        }
        save_registry(registry)
        logger.info(f"[{paper_id}] 上传已接收，状态=processing，文件={file.filename}，大小={len(content)} bytes")

        # 异步执行处理管道
        def run_pipeline():
            try:
                from Paper_RAG.core.main import process_pdf_pipeline, PipelineAbortedError
                import re

                logger.info(f"[{paper_id}] 开始异步处理: {file.filename}")

                # 取消检查闭包：paper 不存在或状态为 deleting 时停止
                def should_abort():
                    try:
                        reg = load_registry()
                        papers = reg.get("papers", reg)
                        if paper_id not in papers:
                            logger.info(f"[{paper_id}] abort: 记录已不存在")
                            return True
                        status = papers[paper_id].get("status", "")
                        if status in ("deleting",):
                            logger.info(f"[{paper_id}] abort: 状态为 {status}")
                            return True
                        return False
                    except Exception:
                        return False

                result = process_pdf_pipeline(tmp_path, file.filename, should_abort=should_abort)

                # 检查是否因删除而中止
                if "检测到删除请求" in result:
                    logger.info(f"[{paper_id}] 处理已中止（并发删除）: {result}")
                    return

                # 从返回字符串解析 chunk 数量，格式："成功处理：xxx.pdf，生成 N 个 chunks"
                match = re.search(r"生成 (\d+) 个 chunks", result)
                if match:
                    chunk_count = int(match.group(1))
                elif "文档已存在" in result or "已跳过处理" in result:
                    # 去重跳过场景：从 md5_records 回填历史 chunk_count，避免写成 completed+0
                    chunk_count = 0
                    try:
                        md5_records_path = os.path.join(DATA_DIR, "md5_records.json")
                        if os.path.exists(md5_records_path):
                            with open(md5_records_path, "r", encoding="utf-8") as f:
                                md5_records = json.load(f)
                            for full_md5, meta in md5_records.items():
                                if full_md5.startswith(paper_id):
                                    chunk_count = int(meta.get("chunk_count", 0) or 0)
                                    break
                    except Exception:
                        pass
                else:
                    chunk_count = 0

                # 双重检查：回写 completed 前再次确认未被删除
                try:
                    reg = load_registry()
                    papers = reg.get("papers", reg)
                    if paper_id not in papers:
                        logger.info(f"[{paper_id}] completed 回写跳过：记录已不存在")
                        return
                    if papers[paper_id].get("status") == "deleting":
                        logger.info(f"[{paper_id}] completed 回写跳过：状态为 deleting")
                        return
                except Exception:
                    return

                update_paper_status(paper_id, "completed", chunk_count=chunk_count)
                logger.info(f"[{paper_id}] 处理完成，chunk_count={chunk_count}")
            except PipelineAbortedError as e:
                logger.info(f"[{paper_id}] 处理中止（PipelineAbortedError）: {e}")
            except BaseException as e:
                logger.error(f"[PIPELINE] 处理失败 {paper_id}: {e}")
                # failed 回写前同样检查删除状态
                try:
                    reg = load_registry()
                    papers = reg.get("papers", reg)
                    if paper_id not in papers:
                        logger.info(f"[{paper_id}] failed 回写跳过：记录已不存在")
                        return
                    if papers[paper_id].get("status") == "deleting":
                        logger.info(f"[{paper_id}] failed 回写跳过：状态为 deleting")
                        return
                except Exception:
                    return
                update_paper_status(paper_id, "failed", error_msg=str(e))
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        threading.Thread(target=run_pipeline, daemon=True).start()

        return UploadResponse(status="processing", paper_id=paper_id)

    except Exception as e:
        logger.exception(f"[/api/upload] 处理异常: {e}")
        raise HTTPException(status_code=500, detail=f"PDF 处理失败: {str(e)}")


# ── 文件夹管理接口 ────────────────────────────────────────────────────────────

@app.post("/api/folders", response_model=FolderNode)
async def api_create_folder(request: CreateFolderRequest):
    """新建文件夹。

    Args:
        request.name: 文件夹名称
        request.parent_id: 父文件夹 ID，可选

    Returns:
        新建的 folder 对象
    """
    try:
        folder = create_folder(request.name, request.parent_id)
        return FolderNode(
            id=folder["id"],
            name=folder["name"],
            parent_id=folder.get("parent_id"),
            is_system=folder.get("is_system", False),
            children=[],
        )
    except Exception as e:
        logger.exception(f"[/api/folders POST] 创建文件夹异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/folders/{folder_id}", response_model=FolderNode)
async def api_rename_folder(folder_id: str, request: RenameFolderRequest):
    """重命名文件夹。

    Args:
        folder_id: 文件夹 ID
        request.name: 新名称

    Returns:
        更新后的 folder 对象

    Raises:
        400: 尝试重命名系统文件夹
        404: 文件夹不存在
    """
    try:
        folder = rename_folder(folder_id, request.name)
        return FolderNode(
            id=folder["id"],
            name=folder["name"],
            parent_id=folder.get("parent_id"),
            is_system=folder.get("is_system", False),
            children=[],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"[/api/folders/{folder_id} PATCH] 重命名异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/folders/{folder_id}", response_model=DeleteFolderResponse)
async def api_delete_folder(folder_id: str):
    """删除文件夹。

    Args:
        folder_id: 文件夹 ID

    Returns:
        {"deleted_folders": [...], "moved_papers": [...]}

    Raises:
        400: 尝试删除系统文件夹
    """
    try:
        result = delete_folder(folder_id)
        return DeleteFolderResponse(
            deleted_folders=result["deleted_folders"],
            moved_papers=result["moved_papers"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"[/api/folders/{folder_id} DELETE] 删除异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── 文档管理接口 ──────────────────────────────────────────────────────────────

@app.patch("/api/documents/{paper_id}/folder")
async def api_move_paper(paper_id: str, request: MovePaperRequest):
    """将文档移入指定文件夹。

    Args:
        paper_id: 文档 ID
        request.folder_id: 目标文件夹 ID

    Returns:
        更新后的 paper 对象

    Raises:
        404: paper_id 或 folder_id 不存在
    """
    try:
        paper = move_paper_to_folder(paper_id, request.folder_id)
        return paper
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"[/api/documents/{paper_id}/folder PATCH] 移动文档异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


from pydantic import BaseModel as PydanticBaseModel

class BatchDeleteRequest(PydanticBaseModel):
    paper_ids: list[str]


@app.delete("/api/documents/batch")
async def api_delete_documents_batch(request: BatchDeleteRequest):
    """批量删除文档。

    Args:
        paper_ids: 要删除的文档 ID 列表

    Returns:
        {"deleted": [...], "failed": [...]}
    """
    deleted: list[str] = []
    failed: list[dict] = []
    for paper_id in request.paper_ids:
        try:
            registry = load_registry()
            papers = registry.get("papers", registry)
            if paper_id not in papers:
                failed.append({"paper_id": paper_id, "reason": "not found"})
                continue
            current_status = papers[paper_id].get("status", "")
            if current_status == "processing":
                papers[paper_id]["status"] = "deleting"
                save_registry(registry)
            result = delete_single_paper(paper_id, registry)
            if result["status"] == "completed":
                if paper_id in registry.get("papers", registry):
                    del registry["papers"][paper_id]
                    save_registry(registry)
                deleted.append(paper_id)
            else:
                failed.append({"paper_id": paper_id, "reason": result.get("detail", result["status"])})
        except Exception as e:
            failed.append({"paper_id": paper_id, "reason": str(e)})
    return {"deleted": deleted, "failed": failed}


@app.delete("/api/documents/{paper_id}", response_model=DeleteDocumentResponse)
async def api_delete_document(paper_id: str):
    """删除文档。

    Args:
        paper_id: 文档 ID

    Returns:
        DeleteDocumentResponse with real cleanup status
    """
    try:
        registry = load_registry()

        # 检查 paper 是否存在
        papers = registry.get("papers", registry)
        if paper_id not in papers:
            raise HTTPException(
                status_code=404,
                detail=f"文档 {paper_id} 不存在"
            )

        # 若当前状态为 processing，先置为 deleting，通知后台线程停止
        current_status = papers[paper_id].get("status", "")
        if current_status == "processing":
            papers[paper_id]["status"] = "deleting"
            save_registry(registry)
            logger.info(f"[{paper_id}] 删除接口：已将状态从 processing 改为 deleting")

        result = delete_single_paper(paper_id, registry)

        # 仅在 completed 时才从 registry 硬删除
        if result["status"] == "completed":
            if paper_id in registry.get("papers", registry):
                del registry["papers"][paper_id]
                save_registry(registry)
            return DeleteDocumentResponse(
                status="deleted",
                delete_status="completed",
                detail=result.get("detail", ""),
                paper_id=result.get("paper_id"),
                filename=result.get("filename"),
                vectors_before=result.get("vectors_before"),
                vectors_after=result.get("vectors_after"),
                local_deleted=result.get("local_deleted"),
            )

        # partial 或 failed：不硬删除，返回失败语义
        if result["status"] == "partial":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"部分删除失败：{result.get('detail', '')}"
                    f"（paper_id={paper_id}, vectors_before={result.get('vectors_before')}, "
                    f"vectors_after={result.get('vectors_after')}, local_deleted={result.get('local_deleted')}）"
                )
            )

        # failed
        raise HTTPException(
            status_code=500,
            detail=(
                f"删除失败：{result.get('detail', '')}"
                f"（paper_id={paper_id}, error={result.get('error_msg', 'unknown')}）"
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[/api/documents/{paper_id} DELETE] 删除异常: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@app.post("/api/documents/{paper_id}/retry")
async def retry_document(paper_id: str):
    """重试失败的文档处理。

    1. 校验状态必须为 failed
    2. 清理所有中间数据（Qdrant / 派生文件 / md5_records）
    3. 重置 registry 状态为 processing，绑定新 task_token
    4. 启动新的异步处理线程（使用持久化的 source.pdf）
    """
    try:
        # ── 状态校验 ─────────────────────────────────────────────────────────
        registry = load_registry()
        papers = registry.get("papers", registry)
        if paper_id not in papers:
            raise HTTPException(status_code=404, detail=f"文档 {paper_id} 不存在")

        current_status = papers[paper_id].get("status", "")
        if current_status != "failed":
            raise HTTPException(
                status_code=409,
                detail="只有失败状态的条目可以重试"
            )

        # ── 持久化 PDF 路径校验 ───────────────────────────────────────────────
        source_pdf_path = os.path.join("data", "papers", paper_id, "source.pdf")
        if not os.path.exists(source_pdf_path):
            raise HTTPException(
                status_code=400,
                detail="源文件不存在，请重新上传"
            )

        original_filename = papers[paper_id].get("original_filename", paper_id)

        # ── 执行清理 ─────────────────────────────────────────────────────────
        from Paper_RAG.core.retry_utils import cleanup_for_retry
        cleanup_result = cleanup_for_retry(paper_id)
        if not cleanup_result.get("success"):
            logger.error(f"[{paper_id}] 清理失败: {cleanup_result}")
            raise HTTPException(status_code=500, detail="清理失败，请重试")

        # ── 绑定新 task_token ────────────────────────────────────────────────
        task_token = str(uuid.uuid4())
        registry = load_registry()
        papers = registry.get("papers", registry)
        papers[paper_id]["task_token"] = task_token
        save_registry(registry)

        logger.info(f"[{paper_id}] 重试已启动，task_token={task_token}")

        # ── 启动重试线程 ────────────────────────────────────────────────────
        def run_retry_pipeline():
            try:
                from Paper_RAG.core.main import process_pdf_pipeline, PipelineAbortedError
                import re

                logger.info(f"[{paper_id}] 重试异步处理开始: {original_filename}")

                def should_abort():
                    try:
                        reg = load_registry()
                        p = reg.get("papers", reg).get(paper_id, {})
                        if p.get("task_token") != task_token:
                            logger.info(f"[{paper_id}] abort: task_token 不匹配（{task_token} != {p.get('task_token')}）")
                            return True
                        if p.get("status") == "deleting":
                            logger.info(f"[{paper_id}] abort: 状态为 deleting")
                            return True
                        return False
                    except Exception:
                        return False

                result = process_pdf_pipeline(source_pdf_path, original_filename, should_abort=should_abort)

                if "检测到删除请求" in result:
                    logger.info(f"[{paper_id}] 重试处理已中止（并发删除）")
                    return

                match = re.search(r"生成 (\d+) 个 chunks", result)
                chunk_count = int(match.group(1)) if match else 0

                try:
                    reg = load_registry()
                    p = reg.get("papers", reg).get(paper_id, {})
                    if paper_id not in reg.get("papers", reg):
                        logger.info(f"[{paper_id}] completed 回写跳过：记录已不存在")
                        return
                    if p.get("task_token") != task_token:
                        logger.info(f"[{paper_id}] completed 回写跳过：task_token 已变更")
                        return
                    if p.get("status") == "deleting":
                        logger.info(f"[{paper_id}] completed 回写跳过：状态为 deleting")
                        return
                except Exception:
                    return

                update_paper_status(paper_id, "completed", chunk_count=chunk_count)
                logger.info(f"[{paper_id}] 重试处理完成，chunk_count={chunk_count}")
            except PipelineAbortedError as e:
                logger.info(f"[{paper_id}] 重试处理中止（PipelineAbortedError）: {e}")
            except BaseException as e:
                logger.error(f"[PIPELINE] 重试处理失败 {paper_id}: {e}")
                try:
                    reg = load_registry()
                    p = reg.get("papers", reg).get(paper_id, {})
                    if paper_id not in reg.get("papers", reg):
                        return
                    if p.get("task_token") != task_token:
                        return
                    if p.get("status") == "deleting":
                        return
                except Exception:
                    return
                update_paper_status(paper_id, "failed", error_msg=str(e))

        threading.Thread(target=run_retry_pipeline, daemon=True).start()
        return {"paper_id": paper_id, "status": "processing"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[/api/documents/{paper_id}/retry] 重试异常: {e}")
        raise HTTPException(status_code=500, detail=f"重试失败: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)

