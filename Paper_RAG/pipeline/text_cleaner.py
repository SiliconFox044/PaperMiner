"""文本清洗模块 - 清洗 MinerU 输出的 Markdown 内容。"""

import re


def clean_markdown(markdown_text: str) -> str:
    """清洗 MinerU 输出的 Markdown 文本。

    Args:
        markdown_text: MinerU 输出的原始 Markdown 字符串

    Returns:
        仅保留标题与正文段落的清洗后 Markdown 字符串
    """
    lines = markdown_text.split('\n')
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()

        # 跳过空行（后续统一处理）
        if not stripped:
            cleaned_lines.append('')
            continue

        # 保留标题行（# ## ###）
        if stripped.startswith('#'):
            cleaned_lines.append(stripped)
            continue

        # 跳过页眉/页脚（常见模式）
        if _is_page_header_footer(stripped):
            continue

        # 跳过页码（独立数字或常见格式）
        if _is_page_number(stripped):
            continue

        # 跳过参考文献节标题
        if _is_reference_section(stripped):
            continue

        # 跳过公式残留（LaTeX 模式）
        if _is_formula_remnant(stripped):
            continue

        # 跳过图片占位符
        if _is_image_placeholder(stripped):
            continue

        # 移除行内引用标记【1-3位数字】，保留 4 位及以上（如年份）
        stripped = _remove_inline_citations(stripped)

        # 保留该行
        cleaned_lines.append(stripped)

    # 拼接各行并规范化多余空行
    text = '\n'.join(cleaned_lines)
    # 将 3 个及以上连续换行替换为双换行
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 去除首尾空白
    text = text.strip()

    return text


def _is_page_header_footer(line: str) -> bool:
    """判断行是否为页眉或页脚。"""
    # 常见页眉/页脚模式
    patterns = [
        r'^第\d+页$',
        r'^Page \d+$',
        r'^\d+/\d+$',
        r'^—\s*\d+\s*—$',
    ]
    return any(re.match(p, line) for p in patterns)


def _is_page_number(line: str) -> bool:
    """判断行是否为独立页码。"""
    return bool(re.match(r'^\d+$', line))


def _is_reference_section(line: str) -> bool:
    """判断行是否为参考文献/书目节标题。"""
    ref_patterns = [
        r'^参考文献?$',
        r'^参考文獻$',
        r'^References?$',
        r'^Bibliography$',
        r'^致谢$',
        r'^Acknowledgments?$',
    ]
    return any(re.match(p, line, re.IGNORECASE) for p in ref_patterns)


def _is_formula_remnant(line: str) -> bool:
    """判断行是否包含公式残留内容。"""
    # 跳过以 LaTeX/数学模式为主的短行
    if re.search(r'\\\w+\{[^}]*\}', line) and len(line) < 50:
        return True
    if re.match(r'^\s*[\[\(][\w\s]+[\]\)]\s*$', line):
        return True
    return False


def _is_image_placeholder(line: str) -> bool:
    """判断行是否为图片占位符。"""
    patterns = [
        r'^!\[.*\]\(.*\)$',
        r'^<img.*>$',
        r'^图\s*\d+.*$',
        r'^Figure\s*\d+.*$',
    ]
    return any(re.match(p, line, re.IGNORECASE) for p in patterns)


def _remove_inline_citations(line: str) -> str:
    """移除行内引用标记【1-3位数字】，保留 4 位及以上（如年份）。

    Args:
        line: 输入行

    Returns:
        移除 1-3 位数字引用标记后的行，4 位及以上的引用标记保留
    """
    # 【1-3位数字】中文方括号
    line = re.sub(r'【\d{1,3}】', '', line)
    # 〔1-3位数字〕中文方括号（另一种写法，如〔2〕〔34〕〔39〕）
    line = re.sub(r'〔\d{1,3}〕', '', line)
    # 带圈数字 ① ② ③ ... ⑳ (U+2460–U+24FF)
    line = re.sub(r'[\u2460-\u24ff]', '', line)
    return line
