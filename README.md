# PaperRAG 系统
基于检索增强生成（Retrieval-Augmented Generation）技术的法学论文处理系统，将你的文献库论文**向量化**。
本系统既能够针对特定论点进行论证支撑度分析，也能根据全库文献对用户问题进行回答。你不必再为自己的论点找不到支撑文献而头疼；也不必频繁新开AI聊天窗口，每次上传几十篇文献，结果没问几个问题就弹出上下文限制。
系统采用前后端分离架构，前端基于 React 构建，后端基于 FastAPI，核心 RAG 流程由 LangChain 驱动。

## 技术栈

### 前端

- **框架**: React 18 + Vite 6
- **UI 库**: Tailwind CSS 4 + shadcn/ui + Radix UI
- **动画**: Framer Motion
- **图标**: Lucide React

### 后端

- **框架**: FastAPI 0.110 + Uvicorn
- **RAG 核心**: LangChain + LangChain-Community
- **向量数据库**: Qdrant（本地持久化）
- **Embedding**: 智谱 AI Embedding-3（2048 维）
- **重排序**: SiliconFlow BGE Reranker
- **PDF 解析**: MinerU

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│  前端 (Vite + React, port 5173)                         │
│  ├─ 文档库 ─── PDF 上传与文件夹管理                       │
│  ├─ 观点搜索 ── 向量检索 + 重排序 + LLM 分析              │
│  └─ 知识问答 ── 流式问答 + 原文引用                       │
└────────────────────────────┬────────────────────────────┘
                             │ HTTP REST + SSE
                             ▼
┌──────────────────────────────────────────────────────────┐
│  后端 (FastAPI, port 8001)                               │
│  ├─ /api/retrieve  ─── 观点搜索                           │
│  ├─ /api/answer    ─── 知识问答                           │
│  ├─ /api/upload   ─── PDF 上传与异步处理                   │
│  ├─ /api/documents/* ─ 文档库 CRUD                        │
│  └─ /api/folders/* ── 文件夹 CRUD                         │
└────────────────────────────┬─────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
  Qdrant (向量)       data/papers/ (源文件)     LLM API
  data/qdrant/        data/paper_registry.json  (SiliconFlow/ZhipuAI)
```

## 目录结构

```
TEST002/
├── frontend/                    # React 前端
│   └── src/app/
│       ├── App.tsx              # 根组件（标签页路由）
│       ├── api.ts               # API 客户端
│       ├── components/
│       │   ├── document-library.tsx  # 模块一：文档库
│       │   ├── OpinionSearch.tsx      # 模块二：观点搜索
│       │   └── knowledge-qa.tsx       # 模块三：知识问答
│       └── context/
│           └── DocumentTreeContext.tsx # 文件夹树共享状态
├── Paper_RAG/                   # RAG 核心库
│   ├── core/
│   │   ├── main.py              # PDF 处理流程编排
│   │   └── batch_processor.py   # 批量处理
│   ├── pipeline/
│   │   ├── pdf_parser.py        # MinerU PDF 解析
│   │   ├── text_cleaner.py      # Markdown 清洗
│   │   ├── chunk_splitter.py    # 分块（Token 边界控制）
│   │   ├── embedding.py         # 批量 Embedding
│   │   └── vector_store.py       # Qdrant 向量存储
│   ├── retrieval/
│   │   └── retrieval.py         # 向量检索 + BGE 重排序
│   ├── generation/
│   │   └── generation.py        # LLM 问答与观点分析链
│   ├── registry/
│   │   ├── paper_registry.py    # 文档元数据与文件夹树
│   │   └── md5_records.py        # MD5 去重记录
│   └── utils/
│       ├── inspector.py          # 检查点保存与解析
│       └── progress.py          # 结构化日志
├── server.py                    # FastAPI 服务入口
├── scripts/
│   ├── clean_orphan_chunks.py   # 清理孤立向量
│   └── reindex_all_papers.py    # 重新索引所有文档
└── data/                        # 数据目录
    ├── papers/                  # 源 PDF 文件
    ├── qdrant/                  # Qdrant 向量数据库
    ├── paper_registry.json      # 文档注册表
    └── md5_records.json         # MD5 去重记录
```

## 核心功能

### 模块一：文档库
本模块采用类Zotero的文献库管理设计，**上传论文pdf文档至系统，自动完成解析、清洗、分块和向量化存储。支持文件夹组织管理、批量删除、失败重试和批量拖拽排序**。

**技术线**: PDF → MinerU 解析 → Markdown 清洗 → Token 分块 → ZhipuAI Embedding → Qdrant 向量存储

### 模块二：观点搜索
本模块负责帮助你进行论点的支撑度分析。你可以直接输入你论文中的某一段内容，系统会在限定的文献范围内，自动检索能够支持输入内容所含论点的原文片段，并对论证支持度进行分析。**你可以用这个模块分析某部分论点的引注是否全面、自己的论证是否充分，或单纯补充脚注。**

**技术线**：系统执行向量检索 + BGE 重排序，再由 LLM 生成结构化分析结果。支持会话历史保存。

### 模块三：知识问答
本模块负责提供类网页AI对话式的知识库问答功能。RAG技术保证文献库的数量可以扩充得足够大，检索召回和Rerank环节能保证检索到最符合问题的片段，然后交给Deepseek深度思考，最终回答你的问题。
已要求Deepseek严格参考文献片段回答，最大化降低幻觉。如果文献库中不包含相关信息，Deepseek会直接说明缺乏参考信息。


## 快速开始

### 环境要求

- Node.js ≥ 18
- Python ≥ 3.10
- Qdrant（本地运行）

### 安装依赖

pip install -r requirements.txt


### 配置环境变量
在Paper_RAG/config/.env中填写对应的api key

1.DEEPSEEK_API_KEY=
模块二论证支持度分析 & 模块三知识问答 依赖，1块够用几十次。

2.ZHIPU_API_KEY=
Embedding依赖，新人注册有免费token

3.SILICONFLOW_API_KEY=
#Rerank重排环节依赖，免费

4.MINERU_API_KEY=
#pdf解析依赖，免费


### 启动服务
# 启动后端（端口 8000）
python server.py 
#或者直接输入以下命令：
uvicorn server:app --reload --port 8000


# 启动前端（端口 5173）
cd frontend
pnpm install   # 首次克隆后执行，之后不需要重复
pnpm dev
然后在任意浏览器中打开：http://localhost:5173/


## API 文档

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/upload` | POST | 上传 PDF 文件 |
| `/api/documents` | GET | 获取文档列表与文件夹树 |
| `/api/documents/{id}` | DELETE | 删除文档 |
| `/api/documents/{id}/retry` | POST | 重试失败文档 |
| `/api/folders` | POST | 创建文件夹 |
| `/api/folders/{id}` | PATCH | 重命名文件夹 |
| `/api/folders/{id}/move` | POST | 移动文档至文件夹 |
| `/api/retrieve` | POST | 观点搜索 |
| `/api/answer` | POST | 知识问答（非流式） |
| `/api/answer/stream` | POST | 知识问答（流式） |

## 数据存储

| 路径 | 说明 |
|------|------|
| `data/papers/` | 原始 PDF 文件 | （论文的原始pdf与清洗后的chunks.json文件存储在此处，每个论文单独文件夹）
| `data/qdrant/` | Qdrant 向量数据库 | (chunks向量化后存储位置)
| `data/paper_registry.json` | 文档元数据与文件夹结构 | （模块一文献管理界面各类数据的核心存储区域）
| `data/md5_records.json` | MD5 去重记录 |

## 维护脚本

```bash
# 清理孤立向量（文档已删除但向量残留）
python scripts/clean_orphan_chunks.py

# 重新索引所有文档
python scripts/reindex_all_papers.py
```
