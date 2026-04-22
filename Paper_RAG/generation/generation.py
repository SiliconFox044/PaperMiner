"""Generation 模块 - 基于 LLM 的法律 RAG 生成链，支持引用输出。"""

import os
import re
from typing import List

from langchain_core.documents import Document
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

try:
    from langchain_openai import ChatOpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


# 用于格式化每个 context chunk 的文档 prompt 模板
DOCUMENT_PROMPT_TEMPLATE = """【片段 {index}】
文件名：{source_file}
标题路径：{headings}
正文：{page_content}
"""


def format_documents(docs: List[Document]) -> str:
    """将文档及其 metadata 格式化为 prompt 所需的上下文字符串。

    Args:
        docs: Document 对象列表

    Returns:
        格式化后的上下文字符串
    """
    formatted = []
    for i, doc in enumerate(docs, start=1):
        source_file = doc.metadata.get("source_file", "未知")
        headings = doc.metadata.get("headings", ["未知"])
        if isinstance(headings, list):
            headings = " > ".join(headings)

        formatted.append(
            DOCUMENT_PROMPT_TEMPLATE.format(
                index=i,
                source_file=source_file,
                headings=headings,
                page_content=doc.page_content.strip()
            )
        )
    return "\n".join(formatted)


# ── 主分析师 prompt ─────────────────────────────────────────────────────────────

ANALYST_PROMPT = """# 角色 (Role)
你是一位严谨的法学论文分析师。你的核心任务是：比对用户提出的【观点】与检索提供的【法学论文文献片段】，评估两者之间的语义关联与逻辑印证关系，并给出带有严格引用的客观判定。

# 约束条件 (Constraints)
1. 事实绝对隔离：你只能基于提供的 [文献片段] 进行判定，绝不允许引入任何外部知识或常识。
2. 拒绝强行关联：如果文献内容与用户观点仅有关键词重合，但逻辑上并无支撑关系，必须判定为"无关"。
3. 关注边界条件：特别注意文献中是否存在"但是"、"前提是"、"仅在...情况下"等限制性表述。
4. 原文引用：所有提取的证据必须是文献中的原话，严禁篡改或概括。

# 输入数据 (Input)

## [文献片段 Context]
{context}

## [用户观点 Claim]
{query}

# [server.py 对接] 要求 LLM 返回结构化 JSON，便于前端解析 sources
# 输出格式 (Output)
你必须且只能返回一个合法的 JSON 对象，不要输出任何 JSON 以外的内容，
不要添加 markdown 代码块标记（如 ```json）。

JSON 结构如下：
{{
  "answer": "在此字段内，严格按以下 Markdown 结构输出完整分析：\n### 1. 判定结论\n*(从以下四项中选一，保留 emoji)*\n- 🟢 充分支持 / 🟡 部分支持 / 🔴 明确反驳 / ⚪ 缺乏支撑\n\n### 2. 逻辑比对分析\n*(200-300字，解释判定理由)*\n\n### 3. 核心证据提取\n*(逐条列出原文，无则输出'无')*\n- 证据1：'...' (来源：[文件名])",
  "sources": [
    {{
      "file": "来源文件名（不含路径）",
      "path": "章节路径，如无则留空字符串",
      "excerpt": "最相关的原文片段，不超过100字"
    }}
  ]
}}
"""
# [/server.py 对接]


# ── 知识问答 QA prompt ─────────────────────────────────────────────────────────

QA_PROMPT = """# 角色 (Role)
你是一位严谨的法学文献问答助手。你的唯一知识来源是系统传来的、与用户问题相关的论文片段。
你的任务是基于这些文献片段，用简洁易懂的语言直接回答用户的问题。

# 铁律约束 (Hard Rules)
1. 严格禁止使用文献片段以外的任何知识，包括你自身的训练知识。
2. 如果文献片段中没有足够的信息回答问题，必须直接告知用户：
   "当前文献库中缺乏回答该问题的相关内容。"
   不得推测、补充或变通作答。
3. 所有关键结论必须标注来源文件名。
4. 回答风格要求周全详细，充分结合检索到的文献回答用户的问题，详细说明答案，帮助用户理解答案。

# 输入数据 (Input)

## [文献片段 Context]
{context}

## [用户问题 Question]
{query}

# 输出格式 (Output)
你必须且只能返回一个合法的 JSON 对象，不要输出任何 JSON 以外的内容，
不要添加 markdown 代码块标记（如 ```json）。

JSON 结构如下：
{{
  "answer": "在此字段内按以下结构回答：**答案如下：**\n以分列要点的形式详细回答用户的问题（200-400字），每个要点单独一行，务必保证逻辑严谨，一切轮带你都要从引用的论文中推理得出，引用原文时标注来源。\n\n**来源文献**\n列出本次回答所依据的文献名称。\n\n如文献库中无相关内容，直接输出：当前文献库中缺乏回答该问题的相关内容。",
  "sources": [
    {{
      "file": "来源文件名（不含路径）",
      "path": "章节路径，如无则留空字符串",
      "excerpt": "支撑回答的原文片段，不超过100字"
    }}
  ]
}}
"""


def create_generation_chain():
    """创建法律论文分析用的 LLM 生成链。

    Returns:
        接收 documents 和 query，返回带引用的结构化分析结果的生成链
    """
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY environment variable is not set")

    llm = ChatOpenAI(
        model="deepseek-reasoner",
        api_key=api_key,
        base_url="https://api.deepseek.com",
        temperature=0.3
    )

    prompt = PromptTemplate.from_template(ANALYST_PROMPT)

    chain = (
        RunnablePassthrough()
        | {
            "query": RunnablePassthrough(),
            "context": lambda x: format_documents(x["documents"])
        }
        | prompt
        | llm
        | StrOutputParser()
    )

    return chain


def create_qa_chain():
    """知识问答专用生成链，使用 QA_PROMPT"""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY environment variable is not set")

    llm = ChatOpenAI(
        model="deepseek-reasoner",
        api_key=api_key,
        base_url="https://api.deepseek.com",
        temperature=0.2
    )

    prompt = PromptTemplate.from_template(QA_PROMPT)

    chain = (
        RunnablePassthrough()
        | {
            "query": RunnablePassthrough(),
            "context": lambda x: format_documents(x["documents"])
        }
        | prompt
        | llm
        | StrOutputParser()
    )

    return chain


def generate_answer(chain, documents: List[Document], question: str) -> str:
    """使用生成链生成结构化分析结果。

    Args:
        chain: 生成链
        documents: 作为上下文的检索文档
        question: 用户问题或待分析观点

    Returns:
        结构化分析结果
    """
    return chain.invoke({"documents": documents, "question": question})


def _extract_json(text: str) -> str:
    """从 LLM 输出中提取第一个完整 JSON 对象"""
    if not text or not text.strip():
        return '{"answer": null, "sources": []}'
    # 去除可能的 markdown 代码块标记
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()
    # 提取第一个完整 {} 对象
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0)
    return '{"answer": null, "sources": []}'


def create_qa_chain_stream():
    """知识问答流式生成链，返回 token 流。"""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY environment variable is not set")

    llm = ChatOpenAI(
        model="deepseek-reasoner",
        api_key=api_key,
        base_url="https://api.deepseek.com",
        temperature=0.3,
        streaming=True
    )

    prompt = PromptTemplate.from_template(QA_PROMPT)

    chain = (
        RunnablePassthrough()
        | {
            "query": RunnablePassthrough(),
            "context": lambda x: format_documents(x["documents"])
        }
        | prompt
        | llm
        | StrOutputParser()
    )

    return chain
