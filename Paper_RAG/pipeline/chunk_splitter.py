"""Chunk Splitter 模块 - 使用 LangChain 将 Markdown 切分为 chunk。"""

import re
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter


def split_chunks(markdown_text: str) -> List[Document]:
    """将 Markdown 文本切分为保留标题层级的 chunk 列表。

    先通过 regex 归一化标题级别（H1-H5），再按 H1-H5 标题切分，
    最后按单换行进一步切分。每个 chunk 内容以标题路径开头。

    Args:
        markdown_text: 清洗后的 Markdown 字符串

    Returns:
        带有标题 metadata 的 Document 对象列表
    """
    # 步骤 0：归一化标题级别
    # 论文标题保持 H1，各级章节标题依次顺延：
    #   一、二、三、...         → H2 (##)  一级章节（兼容"第X"前缀）
    #   （一）（二）...         → H3 (###) 二级小节（兼容中英文括号、括号内外空格）
    #   1. 2. 3. ...          → H4 (####) 三级条目（编号后必须跟空格）
    #   （1）（2）...          → H5 (#####) 四级条目（兼容中英文括号、括号内外空格）
    markdown_text = re.sub(
        r'^#\s+(第?[一二三四五六七八九十百]+[、，])',
        r'## \1',
        markdown_text,
        flags=re.MULTILINE
    )
    markdown_text = re.sub(
        r'^#\s+([（(]\s*[一二三四五六七八九十]+\s*[）)])',
        r'### \1',
        markdown_text,
        flags=re.MULTILINE
    )
    markdown_text = re.sub(
        r'^#\s+(\d+\.\s)',
        r'#### \1',
        markdown_text,
        flags=re.MULTILINE
    )
    markdown_text = re.sub(
        r'^#\s+([（(]\s*\d+\s*[）)])',
        r'##### \1',
        markdown_text,
        flags=re.MULTILINE
    )

    # 步骤 1：按标题切分（H1-H5）
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[
            ("#", "Header1"),
            ("##", "Header2"),
            ("###", "Header3"),
            ("####", "Header4"),
            ("#####", "Header5"),
        ],
        strip_headers=True
    )

    docs_with_headers = header_splitter.split_text(markdown_text)

    # 步骤 2：用 split_documents 按单换行进一步切分
    text_splitter = RecursiveCharacterTextSplitter(
        separators=["  \n"],
        chunk_size=600,
        chunk_overlap=0,
        length_function=len,
    )

    docs = text_splitter.split_documents(docs_with_headers)

    # 步骤 3：处理每个 chunk
    result_chunks = []
    for doc in docs:
        current_headings = []

        # 优先提取 Header1；若缺失则视为前言/摘要
        h1 = doc.metadata.pop("Header1", None)
        if h1:
            current_headings.append(h1)
        else:
            current_headings.append("前言/摘要")

        # 按顺序追加剩余层级
        for key in ("Header2", "Header3", "Header4", "Header5"):
            val = doc.metadata.pop(key, None)
            if val:
                current_headings.append(val)

        # 将修正后的标题列表写回 metadata
        doc.metadata["headings"] = current_headings

        # 在 page_content 开头插入标题路径
        heading_path = "/".join(current_headings)
        first_line = doc.page_content.lstrip('\n').split('\n', 1)[0].strip()
        if first_line.startswith('#'):
            doc.page_content = doc.page_content.split('\n', 1)[1]
        doc.page_content = f"{heading_path}\n{doc.page_content}"

        result_chunks.append(doc)

    return result_chunks
