/**
 * API client for Legal RAG Backend
 * BASE_URL 从环境变量读取，回退到 http://localhost:8000
 */

// ─── Types ────────────────────────────────────────────────────────────────────

export interface RetrievedDocument {
  id: string;
  source: string;        // 文件名
  similarity: number;     // reranker 相关度分数
  text: string;          // 页面内容
  path?: string;         // 标题元数据
}

export interface Source {
  file: string;
  path: string;
  excerpt: string;
}

export interface AnswerResponse {
  answer: string;
  sources: Source[];
}

export interface FolderNode {
  id: string;
  name: string;
  parent_id?: string | null;
  is_system?: boolean;
  children?: FolderNode[];
  /** 文件节点专属：完整文件名（带.pdf后缀），仅 type === "file" 时有值 */
  filename?: string;
  /** 文件节点专属：所属文件夹ID，仅 type === "file" 时有值 */
  folder_id?: string;
  /** 文件节点专属：处理状态，仅 type === "file" 时有值 */
  status?: "processing" | "ready";
}

export interface DocumentRecord {
  id: string;
  title: string;
  addedDate: string;
  status: "processing" | "ready" | "failed" | "deleting" | "delete_failed" | "pending";
  folder_id: string;
}

export interface DocumentLibraryResponse {
  folders: FolderNode[];
  documents: DocumentRecord[];
}

export interface UploadResponse {
  status: string;
  paper_id: string;
}

// ─── Client ───────────────────────────────────────────────────────────────────

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const json = await res.json();
      if (json && typeof json === "object" && "detail" in json) {
        message = (json as { detail: string }).detail;
      }
    } catch {
      // 使用状态文本作为回退
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const json = await res.json();
      if (json && typeof json === "object" && "detail" in json) {
        message = (json as { detail: string }).detail;
      }
    } catch {
      // 使用状态文本作为回退
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const json = await res.json();
      if (json && typeof json === "object" && "detail" in json) {
        message = (json as { detail: string }).detail;
      }
    } catch {
      // 使用状态文本作为回退
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const json = await res.json();
      if (json && typeof json === "object" && "detail" in json) {
        message = (json as { detail: string }).detail;
      }
    } catch {
      // 使用状态文本作为回退
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

// ─── API Functions ─────────────────────────────────────────────────────────────

/**
 * POST /api/retrieve
 * 观点搜索：向量检索 + Jina rerank + LLM 分析
 */
export async function fetchRetrieve(
  query: string,
  topK: number = 5,
  paperIds?: string[]
): Promise<{
  results: RetrievedDocument[];
  analysis: string | null;
  analysis_sources: Record<string, unknown>[] | null;
}> {
  const body: Record<string, unknown> = {
    query,
    top_k: topK,
  };
  if (paperIds && paperIds.length > 0) {
    body.paper_ids = paperIds;
  }
  const data = await post<{
    results: RetrievedDocument[];
    analysis: string | null;
    analysis_sources: Record<string, unknown>[] | null;
  }>("/api/retrieve", body);
  return data;
}

/**
 * POST /api/answer
 * 知识问答：RAG 生成答案
 */
export async function fetchAnswer(
  question: string,
  paperIds: string[] = [],
  mode: string = "qa"
): Promise<{ answer: string; sources: Source[] }> {
  return post<{ answer: string; sources: Source[] }>("/api/answer", {
    question,
    paper_ids: paperIds,
    mode,
  });
}

/**
 * GET /api/documents
 * 文档库列表（真实请求）
 */
export async function fetchDocuments(): Promise<DocumentLibraryResponse> {
  return get<DocumentLibraryResponse>("/api/documents");
}

/**
 * POST /api/upload
 * PDF 文件上传（真实请求）
 */
export async function fetchUpload(file: File): Promise<{ status: string; paper_id: string }> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${BASE_URL}/api/upload`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const json = await res.json();
      if (json && typeof json === "object" && "detail" in json) {
        message = (json as { detail: string }).detail;
      }
    } catch {
      // 使用状态文本作为回退
    }
    throw new Error(message);
  }
  return res.json() as Promise<{ status: string; paper_id: string }>;
}

/**
 * POST /api/folders
 * 新建文件夹
 */
export async function createFolder(name: string, parentId?: string): Promise<FolderNode> {
  return post<FolderNode>("/api/folders", {
    name,
    parent_id: parentId ?? null,
  });
}

/**
 * PATCH /api/folders/{folder_id}
 * 重命名文件夹
 */
export async function renameFolder(folderId: string, name: string): Promise<FolderNode> {
  return patch<FolderNode>(`/api/folders/${folderId}`, { name });
}

/**
 * DELETE /api/folders/{folder_id}
 * 删除文件夹
 */
export async function deleteFolder(folderId: string): Promise<{ deleted_folders: string[]; moved_papers: string[] }> {
  return del<{ deleted_folders: string[]; moved_papers: string[] }>(`/api/folders/${folderId}`);
}

/**
 * PATCH /api/documents/{paper_id}/folder
 * 将文档移入指定文件夹
 */
export async function movePaperToFolder(paperId: string, folderId: string): Promise<Record<string, unknown>> {
  return patch<Record<string, unknown>>(`/api/documents/${paperId}/folder`, { folder_id: folderId });
}

/**
 * DELETE /api/documents/{paper_id}
 * 删除文档
 *
 * 后端模块A返回结构：
 * - 成功: { status: "deleted", delete_status: "completed", detail, paper_id, ... }
 * - 失败: HTTP 409/500，body为 { detail: string }
 */
export interface DeleteDocumentResponse {
  status: "deleted";
  delete_status: string;
  detail: string;
  paper_id?: string;
  filename?: string;
  vectors_before?: number;
  vectors_after?: number;
  local_deleted?: boolean;
  md5_deleted?: boolean;
  md5_msg?: string;
}

export async function deleteDocument(paperId: string): Promise<DeleteDocumentResponse> {
  return del<DeleteDocumentResponse>(`/api/documents/${paperId}`);
}

/**
 * DELETE /api/documents/batch
 * 批量删除文档，返回成功与失败列表
 */
export interface BatchDeleteResponse {
  deleted: string[];
  failed: { paper_id: string; reason: string }[];
}

export async function batchDeleteDocuments(paperIds: string[]): Promise<BatchDeleteResponse> {
  const res = await fetch(`${BASE_URL}/api/documents/batch`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paper_ids: paperIds }),
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const json = await res.json();
      if (json && typeof json === "object" && "detail" in json) {
        message = (json as { detail: string }).detail;
      }
    } catch {
      // 使用状态文本作为回退
    }
    throw new Error(message);
  }
  return res.json() as Promise<BatchDeleteResponse>;
}

/**
 * POST /api/documents/{paper_id}/retry
 * 重试失败的文档处理
 */
export async function retryDocument(paperId: string): Promise<{ paper_id: string; status: string }> {
  const res = await fetch(`${BASE_URL}/api/documents/${paperId}/retry`, {
    method: "POST",
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const json = await res.json();
      if (json && typeof json === "object" && "detail" in json) {
        message = (json as { detail: string }).detail;
      }
    } catch {
      // 使用状态文本作为回退
    }
    throw new Error(message);
  }
  return res.json() as Promise<{ paper_id: string; status: string }>;
}

/**
 * 轮询文档状态直到完成或失败
 *
 * @param paperId 文档 ID
 * @param onStatusChange 状态变化回调
 * @param intervalMs 轮询间隔（默认 3000ms）
 * @param maxAttempts 最大轮询次数（默认 60）
 */
export async function pollDocumentStatus(
  paperId: string,
  onStatusChange: (status: string) => void,
  intervalMs: number = 3000,
  maxAttempts: number = 60
): Promise<void> {
  let attempts = 0;

  const poll = async (): Promise<void> => {
    if (attempts >= maxAttempts) {
      return;
    }
    attempts++;

    try {
      const data = await fetchDocuments();
      const doc = data.documents.find((d) => d.id === paperId);
      if (doc) {
        onStatusChange(doc.status);
        if (doc.status === "ready" || doc.status === "failed") {
          return;
        }
      }
      // 继续轮询
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
      await poll();
    } catch {
      // 出错时继续轮询
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
      await poll();
    }
  };

  await poll();
}
