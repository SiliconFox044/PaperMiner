"""PDF 解析模块 - 使用 MinerU 解析 PDF 文件。"""

import os
import threading
from typing import List

try:
    from langchain_mineru import MinerULoader
    from langchain_core.documents import Document
except ImportError as e:
    print("\033[91m[ERROR] Failed to import MinerU. Please install: pip install langchain-mineru\033[0m")
    raise RuntimeError("Failed to import MinerU. Please install: pip install langchain-mineru") from e


# MinerU API 解析模式："flash"（快速）或 "precision"（高精度）
# 生产环境默认使用 precision 模式
MINERU_MODE = os.getenv("MINERU_MODE", "precision")

# MinerU API token（从 .env 读取）
MINERU_TOKEN = os.getenv("MINERU_API_KEY")
if MINERU_TOKEN:
    os.environ["MINERU_TOKEN"] = MINERU_TOKEN


_mineru_upload_semaphore = threading.Semaphore(1)


def parse_pdf(file_path: str, mode: str = MINERU_MODE) -> str:
    """使用 MinerU 解析 PDF 文件，返回 Markdown 字符串。

    Args:
        file_path: PDF 文件路径
        mode: MinerU 解析模式 - "flash"（快速）或 "precision"（精准）
              默认取环境变量 MINERU_MODE，或回退为 "precision"

    Returns:
        将所有文档以双换行符拼接而成的 Markdown 字符串

    Raises:
        RuntimeError: 若 MinerU 解析失败
    """
    try:
        loader_kwargs = {
            "source": file_path,
            "mode": mode,
            "language": "ch",
            "table": True,
            "formula": True,
        }
        if MINERU_TOKEN:
            loader_kwargs["token"] = MINERU_TOKEN

        loader = MinerULoader(**loader_kwargs)
        if mode == "precision":
            with _mineru_upload_semaphore:
                docs: List[Document] = loader.load()
        else:
            docs: List[Document] = loader.load()
    except Exception as e:
        print(f"\033[91m[ERROR] Failed to parse PDF with MinerU ({mode} mode): {e}\033[0m")
        raise RuntimeError(f"Failed to parse PDF with MinerU ({mode} mode): {e}") from e

    # 将所有文档用双换行符拼接，形成完整的 Markdown
    markdown_text = "\n\n".join(doc.page_content for doc in docs)
    return markdown_text
