import { useState, useEffect, useRef, useCallback } from "react";
import {
  ChevronRight,
  ChevronDown,
  Folder,
  Loader2,
  CheckCircle2,
  Upload,
  AlertCircle,
  AlertTriangle,
  MoreVertical,
  Pencil,
  Trash2,
  FolderPlus,
  XCircle,
} from "lucide-react";
import {
  fetchDocuments,
  fetchUpload,
  createFolder,
  renameFolder,
  deleteFolder,
  movePaperToFolder,
  deleteDocument,
  batchDeleteDocuments,
  retryDocument,
  type FolderNode as ApiFolderNode,
  type DocumentRecord,
} from "../api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "./ui/alert-dialog";
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuTrigger, ContextMenuSeparator } from "./ui/context-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "./ui/tooltip";

// ─── 类型定义 ─────────────────────────────────────────────────────────────────

interface FolderNodeInternal extends ApiFolderNode {
  isEditing?: boolean;
  editValue?: string;
  newFolderInput?: boolean;
  newFolderValue?: string;
}

// ─── 状态图标组件 ─────────────────────────────────────────────────────────────

function StatusIcon({
  status,
  onRetry,
  onRetryDelete,
}: {
  status: string;
  onRetry?: () => void;
  onRetryDelete?: () => void;
}) {
  if (status === "processing" || status === "deleting") {
    return <Loader2 className="w-4 h-4 text-muted-foreground animate-spin" />;
  }
  if (status === "ready") {
    return <CheckCircle2 className="w-4 h-4 text-green-500" />;
  }
  if (status === "failed") {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <XCircle
              className="w-4 h-4 text-red-500 cursor-pointer"
              onClick={onRetry}
            />
          </TooltipTrigger>
          <TooltipContent>点击重试解析</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }
  if (status === "delete_failed") {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <XCircle
              className="w-4 h-4 text-orange-500 cursor-pointer"
              onClick={onRetryDelete}
            />
          </TooltipTrigger>
          <TooltipContent>点击重试删除</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }
  return null;
}

function StatusText({ status }: { status: string }) {
  if (status === "processing") return "解析中";
  if (status === "deleting") return "删除中";
  if (status === "ready") return "已完成";
  if (status === "failed") return "解析失败";
  if (status === "delete_failed") return "删除失败";
  return "等待解析";
}

// ─── 文件夹树节点 ───────────────────────────────────────────────────────────────

function FolderTreeItem({
  node,
  level = 0,
  selectedFolderId,
  onSelectFolder,
  onRename,
  onDelete,
  onCreateSubfolder,
  onCancelNewFolder,
  onConfirmNewFolder,
  onRenameSubmit,
  dragOverFolderId,
  onDragOver,
  onDragEnter,
  onDragLeave,
  onDrop,
  onDragStart,
}: {
  node: FolderNodeInternal;
  level?: number;
  selectedFolderId: string | null;
  onSelectFolder: (id: string) => void;
  onRename: (id: string) => void;
  onDelete: (id: string) => void;
  onCreateSubfolder: (parentId: string) => void;
  onCancelNewFolder: () => void;
  onConfirmNewFolder: (parentId: string | null, name: string) => void;
  onRenameSubmit: (id: string, name: string) => void;
  dragOverFolderId: string | null;
  onDragOver: (e: React.DragEvent, id: string) => void;
  onDragEnter: (e: React.DragEvent, id: string) => void;
  onDragLeave: (e: React.DragEvent, id: string) => void;
  onDrop: (e: React.DragEvent, id: string) => void;
  onDragStart?: (e: React.DragEvent, id: string) => void;
}) {
  const [expanded, setExpanded] = useState(level === 0);
  // 拖拽高亮
  const isDragOver = dragOverFolderId === node.id;
  // 受控输入的本地 state
  const [editValue, setEditValue] = useState(node.editValue ?? node.name);
  const [newFolderValue, setNewFolderValue] = useState(node.newFolderValue ?? "");
  // 用于延迟 blur，确保点击事件（如确认区域）先触发
  const blurTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelBlurRef = useRef(false);

  const isSelected = selectedFolderId === node.id;
  const isSystem = node.is_system;
  const hasChildren = node.children && node.children.length > 0;

  // 当 node.editValue 变化时同步本地编辑值
  useEffect(() => {
    setEditValue(node.editValue ?? node.name);
  }, [node.editValue, node.name]);

  useEffect(() => {
    setNewFolderValue(node.newFolderValue ?? "");
  }, [node.newFolderValue]);

  const handleNewFolderConfirm = () => {
    if (newFolderValue.trim()) {
      onConfirmNewFolder(node.id, newFolderValue.trim());
    } else {
      onCancelNewFolder();
    }
  };

  // 延迟 blur：在 setTimeout 之前同步捕获 value 和 nodeId，
  // 避免闭包过时导致文件夹消失的问题。
  const handleNewFolderBlur = (e: React.FocusEvent<HTMLInputElement>) => {
    const capturedValue = e.currentTarget.value;
    const capturedNodeId = node.id;
    blurTimerRef.current = setTimeout(() => {
      if (capturedValue.trim()) {
        onConfirmNewFolder(capturedNodeId, capturedValue.trim());
      } else {
        onCancelNewFolder();
      }
    }, 0);
  };

  return (
    <div>
      {/* 非系统文件夹的右键菜单：包裹整行 */}
      {!isSystem && !node.isEditing ? (
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div
              className={`flex items-center gap-1 px-2 py-1.5 rounded-md cursor-pointer transition-colors ${isDragOver ? "ring-2 ring-primary bg-primary/10" : ""} ${
                isSelected ? "bg-sidebar-accent" : "hover:bg-sidebar-accent/50"
              }`}
              style={{ paddingLeft: `${level * 12 + 8}px` }}
              onDragOver={(e) => onDragOver(e, node.id)}
              onDragEnter={(e) => onDragEnter(e, node.id)}
              onDragLeave={(e) => onDragLeave(e, node.id)}
              onDrop={(e) => onDrop(e, node.id)}
            >
              {/* 展开箭头 */}
              <button
                onClick={() => hasChildren && setExpanded(!expanded)}
                className="p-0.5 hover:bg-black/10 rounded"
              >
                {hasChildren ? (
                  expanded ? (
                    <ChevronDown className="w-4 h-4 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-muted-foreground" />
                  )
                ) : (
                  <div className="w-4" />
                )}
              </button>

              {/* 文件夹图标 */}
              <Folder className="w-4 h-4 text-muted-foreground flex-shrink-0" />

              {/* 名称或编辑输入框 */}
              {node.isEditing ? (
                <input
                  autoFocus
                  className="flex-1 text-sm bg-background border border-border rounded px-1 py-0.5"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      if (editValue.trim()) {
                        onRenameSubmit(node.id, editValue.trim());
                      }
                    } else if (e.key === "Escape") {
                      onRename(node.id);
                    }
                  }}
                  onBlur={() => {
                    if (editValue.trim()) {
                      onRenameSubmit(node.id, editValue.trim());
                    } else {
                      onRename(node.id);
                    }
                  }}
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <span
                  className="flex-1 text-sm truncate select-none"
                  onDoubleClick={() => onRename(node.id)}
                  onClick={() => onSelectFolder(node.id)}
                >
                  {node.name}
                </span>
              )}

              {/* ... 按钮 - 左键点击时触发 contextmenu 事件以打开菜单 */}
              <button
                className="p-0.5 hover:bg-black/10 rounded opacity-0 group-hover:opacity-100"
                onClick={(e) => {
                  e.stopPropagation();
                  // 触发合成的 contextmenu 事件以打开菜单
                  const evt = new MouseEvent("contextmenu", {
                    bubbles: true,
                    cancelable: true,
                    clientX: e.clientX,
                    clientY: e.clientY,
                  });
                  e.currentTarget.parentElement?.dispatchEvent(evt);
                }}
              >
                <MoreVertical className="w-3.5 h-3.5 text-muted-foreground" />
              </button>
            </div>
          </ContextMenuTrigger>
          <ContextMenuContent>
            <ContextMenuItem onClick={() => onRename(node.id)}>
              <Pencil className="w-4 h-4 mr-2" />
              重命名
            </ContextMenuItem>
            <ContextMenuItem onClick={() => onCreateSubfolder(node.id)}>
              <FolderPlus className="w-4 h-4 mr-2" />
              新建子文件夹
            </ContextMenuItem>
            <ContextMenuSeparator />
            <ContextMenuItem
              onClick={() => onDelete(node.id)}
              className="text-destructive focus:text-destructive"
            >
              <Trash2 className="w-4 h-4 mr-2" />
              删除文件夹
            </ContextMenuItem>
          </ContextMenuContent>
        </ContextMenu>
      ) : (
        /* 系统文件夹或编辑模式：不包裹右键菜单 */
        <div
          className={`flex items-center gap-1 px-2 py-1.5 rounded-md cursor-pointer transition-colors ${isDragOver ? "ring-2 ring-primary bg-primary/10" : ""} ${
            isSelected ? "bg-sidebar-accent" : "hover:bg-sidebar-accent/50"
          }`}
          style={{ paddingLeft: `${level * 12 + 8}px` }}
          onDragOver={(e) => onDragOver(e, node.id)}
          onDragEnter={(e) => onDragEnter(e, node.id)}
          onDragLeave={(e) => onDragLeave(e, node.id)}
          onDrop={(e) => onDrop(e, node.id)}
        >
          {/* 展开箭头 */}
          <button
            onClick={() => hasChildren && setExpanded(!expanded)}
            className="p-0.5 hover:bg-black/10 rounded"
          >
            {hasChildren ? (
              expanded ? (
                <ChevronDown className="w-4 h-4 text-muted-foreground" />
              ) : (
                <ChevronRight className="w-4 h-4 text-muted-foreground" />
              )
            ) : (
              <div className="w-4" />
            )}
          </button>

          {/* 文件夹图标 */}
          <Folder className="w-4 h-4 text-muted-foreground flex-shrink-0" />

          {/* 名称或编辑输入框 */}
          {node.isEditing ? (
            <input
              autoFocus
              className="flex-1 text-sm bg-background border border-border rounded px-1 py-0.5"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (editValue.trim()) {
                    onRenameSubmit(node.id, editValue.trim());
                  }
                } else if (e.key === "Escape") {
                  onRename(node.id);
                }
              }}
              onBlur={() => {
                if (editValue.trim()) {
                  onRenameSubmit(node.id, editValue.trim());
                } else {
                  onRename(node.id);
                }
              }}
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <span
              className="flex-1 text-sm truncate select-none"
              onClick={() => onSelectFolder(node.id)}
            >
              {node.name}
            </span>
          )}
        </div>
      )}

      {/* 子节点 */}
      {expanded && (
        <div>
          {node.children?.map((child) => (
            <div key={child.id} className="group">
              <FolderTreeItem
                node={child as FolderNodeInternal}
                level={level + 1}
                selectedFolderId={selectedFolderId}
                onSelectFolder={onSelectFolder}
                onRename={onRename}
                onDelete={onDelete}
                onCreateSubfolder={onCreateSubfolder}
                onCancelNewFolder={onCancelNewFolder}
                onConfirmNewFolder={onConfirmNewFolder}
                onRenameSubmit={onRenameSubmit}
                dragOverFolderId={dragOverFolderId}
                onDragOver={onDragOver}
                onDragEnter={onDragEnter}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
                onDragStart={onDragStart}
              />
            </div>
          ))}

          {/* 新建文件夹输入框 */}
          {node.newFolderInput && (
            <div style={{ paddingLeft: `${(level + 1) * 12 + 8}px` }}>
              <div className="flex items-center gap-1 px-2 py-1.5">
                <div className="w-4" />
                <Folder className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                <input
                  autoFocus
                  className="flex-1 text-sm bg-background border border-border rounded px-1 py-0.5"
                  placeholder="文件夹名称"
                  value={newFolderValue}
                  onChange={(e) => setNewFolderValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      if (e.currentTarget.value.trim()) {
                        onConfirmNewFolder(node.id, e.currentTarget.value.trim());
                      } else {
                        onCancelNewFolder();
                      }
                    } else if (e.key === "Escape") {
                      onCancelNewFolder();
                    }
                  }}
                  onBlur={handleNewFolderBlur}
                />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── 主组件 ────────────────────────────────────────────────────────────────────

export function DocumentLibrary({
  folderTree,
  onFolderTreeChange,
}: {
  folderTree: FolderNodeInternal[];
  onFolderTreeChange: () => void;
}) {
  const [folders, setFolders] = useState<FolderNodeInternal[]>(
    () => folderTree as FolderNodeInternal[]
  );

  // folderTree prop 变化时同步
  useEffect(() => {
    setFolders(folderTree as FolderNodeInternal[]);
  }, [folderTree]);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [allDocuments, setAllDocuments] = useState<DocumentRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  const [draggedDocId, setDraggedDocId] = useState<string | null>(null);
  const [dragOverFolderId, setDragOverFolderId] = useState<string | null>(null);
  const [dragEnterCount, setDragEnterCount] = useState<Record<string, number>>({});
  const [selectedDocIds, setSelectedDocIds] = useState<Set<string>>(new Set());

  // 删除确认对话框
  const [deleteTarget, setDeleteTarget] = useState<{
    type: "folder" | "document";
    id: string;
    name: string;
  } | null>(null);

  // 正在删除中的文档 ID 集合（防止重入删除）
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set());

  // 根级别新建文件夹
  const [showRootNewFolder, setShowRootNewFolder] = useState(false);
  const [rootNewFolderValue, setRootNewFolderValue] = useState("");

  // Toast 通知（右下角浮动，不阻塞主界面）
  const [toasts, setToasts] = useState<Array<{ id: string; message: string }>>([]);
  const addToast = (message: string) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((prev: Array<{ id: string; message: string }>) => [...prev, { id, message }]);
    setTimeout(() => {
      setToasts((prev: Array<{ id: string; message: string }>) => prev.filter((t: { id: string; message: string }) => t.id !== id));
    }, 8000);
  };

  const fileInputRef = useRef<HTMLInputElement>(null);

  // 挂载时加载文档
  useEffect(() => {
    loadDocuments();
    return () => {
      // 组件卸载时清理所有正在进行的轮询
      pollingIntervalsRef.current.forEach((intervalId) => clearInterval(intervalId));
      pollingIntervalsRef.current.clear();
    };
  }, []);

  const loadDocuments = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchDocuments();
      setAllDocuments(data.documents);
      // 按选中的文件夹过滤（或显示全部）
      if (selectedFolderId) {
        setDocuments(data.documents.filter((d) => d.folder_id === selectedFolderId));
      } else {
        setDocuments(data.documents);
      }
      // folders 由 App.tsx 统一管理，此处不再 setFolders
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载文档失败");
    } finally {
      setIsLoading(false);
    }
  };

  // 文件夹选择变化时重新过滤文档
  useEffect(() => {
    if (selectedFolderId) {
      setDocuments(allDocuments.filter((d) => d.folder_id === selectedFolderId));
    } else {
      setDocuments(allDocuments);
    }
  }, [selectedFolderId, allDocuments]);

  // ── 轮询清理 ref ────────────────────────────────────────────────────────────
  const pollingIntervalsRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());

  // ── 通用轮询函数 ────────────────────────────────────────────────────────────
  const startPolling = (paperId: string) => {
    const existingInterval = pollingIntervalsRef.current.get(paperId);
    if (existingInterval !== undefined) {
      clearInterval(existingInterval);
      pollingIntervalsRef.current.delete(paperId);
    }

    let attempts = 0;
    const maxAttempts = 25;

    const intervalId = window.setInterval(async () => {
      if (attempts >= maxAttempts) {
        clearInterval(intervalId);
        pollingIntervalsRef.current.delete(paperId);
        setAllDocuments((prev) =>
          prev.map((d) => d.id === paperId && d.status === "processing" ? { ...d, status: "failed" as const } : d)
        );
        setDocuments((prev) =>
          prev.map((d) => d.id === paperId && d.status === "processing" ? { ...d, status: "failed" as const } : d)
        );
        return;
      }
      attempts++;

      try {
        const data = await fetchDocuments();
        const doc = data.documents.find((d) => d.id === paperId);
        if (doc) {
          setAllDocuments((prev) =>
            prev.map((d) => d.id === paperId ? { ...d, status: doc.status } : d)
          );
          setDocuments((prev) =>
            prev.map((d) => d.id === paperId ? { ...d, status: doc.status } : d)
          );
          if (doc.status === "ready" || doc.status === "failed") {
            clearInterval(intervalId);
            pollingIntervalsRef.current.delete(paperId);
          }
        } else {
          clearInterval(intervalId);
          pollingIntervalsRef.current.delete(paperId);
          setAllDocuments((prev) =>
            prev.map((d) => d.id === paperId ? { ...d, status: "failed" as const } : d)
          );
          setDocuments((prev) =>
            prev.map((d) => d.id === paperId ? { ...d, status: "failed" as const } : d)
          );
        }
      } catch { /* 网络抖动，继续下一次轮询 */ }
    }, 8000);

    pollingIntervalsRef.current.set(paperId, intervalId);
  };

  // 上传处理器
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    setIsUploading(true);
    setUploadStatus(null);

    try {
      for (const file of Array.from(files)) {
        const result = await fetchUpload(file);
        setUploadStatus(`已上传：${file.name}`);

        // 立即插入一条处理中的记录
        const processingDoc: DocumentRecord = {
          id: result.paper_id,
          title: file.name.replace(/\.pdf$/i, ""),
          addedDate: new Date().toISOString().slice(0, 10),
          status: "processing",
          folder_id: "uncategorized",
        };
        setAllDocuments((prev) => [...prev, processingDoc]);
        if (!selectedFolderId || selectedFolderId === "uncategorized") {
          setDocuments((prev) => [...prev, processingDoc]);
        }

        // 为每个文件单独管理一个轮询，互不干扰
        // 如果该文件已有轮询在跑（重试场景），先清除
        startPolling(result.paper_id);
      }
    } catch (err) {
      setUploadStatus(`上传失败：${err instanceof Error ? err.message : "未知错误"}`);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  // 文件夹操作
  const handleRename = (folderId: string) => {
    setFolders((prev) =>
      prev.map((f) => _updateFolderTree(f, folderId, (node) => ({
        ...node,
        isEditing: !node.isEditing,
        editValue: node.isEditing ? node.editValue : node.name,
      })))
    );
  };

  const handleRenameSubmit = async (folderId: string, newName: string) => {
    try {
      await renameFolder(folderId, newName);
      setFolders((prev) =>
        prev.map((f) => _updateFolderTree(f, folderId, (node) => ({
          ...node,
          name: newName,
          isEditing: false,
          editValue: undefined,
        })))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "重命名失败");
      return;
    }
    onFolderTreeChange();
  };

  const handleDeleteFolder = async () => {
    if (!deleteTarget || deleteTarget.type !== "folder") return;
    const { id } = deleteTarget;

    try {
      const result = await deleteFolder(id);
      // 重新加载以获取更新后的文件夹树和文档列表
      await loadDocuments();
      onFolderTreeChange();
      // 更新被移动文档的 folder_id
      setAllDocuments((prev) =>
        prev.map((d) =>
          result.moved_papers.includes(d.id) ? { ...d, folder_id: "uncategorized" } : d
        )
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeleteTarget(null);
    }
  };

  const handleCreateSubfolder = (parentId: string) => {
    setFolders((prev) =>
      prev.map((f) => _updateFolderTree(f, parentId, (node) => ({
        ...node,
        newFolderInput: true,
        newFolderValue: "",
      })))
    );
  };

  const handleCancelNewFolder = () => {
    setFolders((prev) => _clearNewFolderInputs(prev));
  };

  const handleConfirmNewFolder = async (parentId: string | null, name: string) => {
    try {
      await createFolder(name, parentId ?? undefined);
      await loadDocuments();
      onFolderTreeChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建文件夹失败");
    }
  };

  // 切换单个复选框
  const toggleSelectDoc = (id: string) => {
    setSelectedDocIds(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  // 批量删除
  const handleBatchDelete = async () => {
    const ids = Array.from(selectedDocIds);
    if (ids.length === 0) return;

    try {
      const result = await batchDeleteDocuments(ids);

      // 仅移除成功删除的条目
      if (result.deleted.length > 0) {
        setAllDocuments((prev) => prev.filter((d) => !result.deleted.includes(d.id)));
        setDocuments((prev) => prev.filter((d) => !result.deleted.includes(d.id)));
      }

      // 失败条目标记为 delete_failed，保留在列表中
      if (result.failed.length > 0) {
        const failedIds = new Set(result.failed.map((f) => f.paper_id));
        setAllDocuments((prev) =>
          prev.map((d) => failedIds.has(d.id) ? { ...d, status: "delete_failed" as const } : d)
        );
        setDocuments((prev) =>
          prev.map((d) => failedIds.has(d.id) ? { ...d, status: "delete_failed" as const } : d)
        );
        setError(`删除完成：成功 ${result.deleted.length} 条，失败 ${result.failed.length} 条`);
      }

      // 清除已成功删除条目的选中状态，失败条目保留选中
      setSelectedDocIds((prev) => {
        const next = new Set(prev);
        result.deleted.forEach((id) => next.delete(id));
        return next;
      });

      onFolderTreeChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "批量删除失败");
    }
  };

  // 文档删除 — 先确认再移除的模式
  const handleDeleteDocument = async () => {
    if (!deleteTarget || deleteTarget.type !== "document") return;
    const { id } = deleteTarget;

    // 防止重入删除
    if (deletingIds.has(id)) return;

    // 标记为删除中（仅本地 state，尚未移除）
    setDeletingIds((prev) => new Set([...prev, id]));
    setAllDocuments((prev) =>
      prev.map((d) => d.id === id ? { ...d, status: "deleting" as const } : d)
    );
    setDocuments((prev) =>
      prev.map((d) => d.id === id ? { ...d, status: "deleting" as const } : d)
    );

    try {
      await deleteDocument(id);
      // 成功：从列表中移除
      setAllDocuments((prev) => prev.filter((d) => d.id !== id));
      setDocuments((prev) => prev.filter((d) => d.id !== id));
      onFolderTreeChange();
    } catch (err) {
      // 失败：标记为 delete_failed，保留条目可见，显示错误
      const msg = err instanceof Error ? err.message : "删除失败";
      setAllDocuments((prev) =>
        prev.map((d) => d.id === id ? { ...d, status: "delete_failed" as const } : d)
      );
      setDocuments((prev) =>
        prev.map((d) => d.id === id ? { ...d, status: "delete_failed" as const } : d)
      );
      setError(msg);
    } finally {
      setDeletingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      setDeleteTarget(null);
    }
  };

  const handleRetry = async (paperId: string) => {
    setAllDocuments((prev: DocumentRecord[]) =>
      prev.map((d: DocumentRecord) => d.id === paperId ? { ...d, status: "processing" as const } : d)
    );
    setDocuments((prev: DocumentRecord[]) =>
      prev.map((d: DocumentRecord) => d.id === paperId ? { ...d, status: "processing" as const } : d)
    );
    try {
      await retryDocument(paperId);
      startPolling(paperId);
    } catch (err) {
      setAllDocuments((prev: DocumentRecord[]) =>
        prev.map((d: DocumentRecord) => d.id === paperId ? { ...d, status: "failed" as const } : d)
      );
      setDocuments((prev: DocumentRecord[]) =>
        prev.map((d: DocumentRecord) => d.id === paperId ? { ...d, status: "failed" as const } : d)
      );
      const msg = err instanceof Error ? err.message : "重试失败";
      if (msg.includes("源文件不存在")) {
        addToast(msg);
      } else {
        setError(msg);
      }
    }
  };

  const handleRetryDelete = async (paperId: string) => {
    if (deletingIds.has(paperId)) return;

    setDeletingIds((prev) => new Set([...prev, paperId]));
    setAllDocuments((prev) =>
      prev.map((d) => d.id === paperId ? { ...d, status: "deleting" as const } : d)
    );
    setDocuments((prev) =>
      prev.map((d) => d.id === paperId ? { ...d, status: "deleting" as const } : d)
    );

    try {
      await deleteDocument(paperId);
      setAllDocuments((prev) => prev.filter((d) => d.id !== paperId));
      setDocuments((prev) => prev.filter((d) => d.id !== paperId));
      onFolderTreeChange();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "删除失败";
      setAllDocuments((prev) =>
        prev.map((d) => d.id === paperId ? { ...d, status: "delete_failed" as const } : d)
      );
      setDocuments((prev) =>
        prev.map((d) => d.id === paperId ? { ...d, status: "delete_failed" as const } : d)
      );
      setError(msg);
    } finally {
      setDeletingIds((prev) => { const next = new Set(prev); next.delete(paperId); return next; });
    }
  };

  // 拖拽功能
  const handleDragStart = (docId: string) => {
    setDraggedDocId(docId);
  };

  const handleDragEnter = (e: React.DragEvent, folderId: string) => {
    e.preventDefault();
    setDragOverFolderId(folderId);
    setDragEnterCount((prev) => ({ ...prev, [folderId]: (prev[folderId] ?? 0) + 1 }));
  };

  const handleDragOver = (e: React.DragEvent, folderId: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverFolderId(folderId);
  };

  const handleDragLeave = (e: React.DragEvent, folderId: string) => {
    const count = dragEnterCount[folderId] ?? 0;
    if (count <= 1) {
      setDragOverFolderId(null);
      setDragEnterCount((prev) => ({ ...prev, [folderId]: 0 }));
    } else {
      setDragEnterCount((prev) => ({ ...prev, [folderId]: count - 1 }));
    }
  };

  const handleDrop = async (e: React.DragEvent, targetFolderId: string) => {
    e.preventDefault();
    setDragOverFolderId(null);
    setDragEnterCount({});

    const rawPaperIds = e.dataTransfer.getData("paperIds");
    const rawPaperId = e.dataTransfer.getData("paperId");

    if (rawPaperIds) {
      const ids: string[] = JSON.parse(rawPaperIds);
      for (const id of ids) {
        await movePaperToFolder(id, targetFolderId);
      }
      setAllDocuments((prev) =>
        prev.map((d) => (ids.includes(d.id) ? { ...d, folder_id: targetFolderId } : d))
      );
      if (selectedFolderId !== null && targetFolderId !== selectedFolderId) {
        setDocuments((prev) => prev.filter((d) => !ids.includes(d.id)));
      } else {
        setDocuments((prev) =>
          prev.map((d) => (ids.includes(d.id) ? { ...d, folder_id: targetFolderId } : d))
        );
      }
      setSelectedDocIds(new Set());
      setDraggedDocId(null);
      return;
    }

    if (!draggedDocId && !rawPaperId) return;
    const docId = rawPaperId || draggedDocId;
    setDraggedDocId(null);

    try {
      await movePaperToFolder(docId, targetFolderId);
      // 更新所有文档（始终执行）
      setAllDocuments((prev) =>
        prev.map((d) => (d.id === docId ? { ...d, folder_id: targetFolderId } : d))
      );
      // 更新右侧文档列表：
      // - 若 selectedFolderId === null（全部文档）：保留文档，更新 folder_id
      // - 若 targetFolderId === selectedFolderId：保留文档
      // - 否则：从列表中移除文档
      if (selectedFolderId === null || targetFolderId === selectedFolderId) {
        setDocuments((prev) =>
          prev.map((d) => (d.id === docId ? { ...d, folder_id: targetFolderId } : d))
        );
      } else {
        setDocuments((prev) => prev.filter((d) => d.id !== docId));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "移动文档失败");
    }
  };

  const handleDragEnd = () => {
    setDraggedDocId(null);
    setDragOverFolderId(null);
    setDragEnterCount({});
  };

  // 辅助函数：递归更新文件夹树中的节点
  const _updateFolderTree = (
    node: FolderNodeInternal,
    targetId: string,
    updater: (n: FolderNodeInternal) => FolderNodeInternal
  ): FolderNodeInternal => {
    if (node.id === targetId) {
      return updater(node);
    }
    if (node.children) {
      return {
        ...node,
        children: node.children.map((c) =>
          _updateFolderTree(c as FolderNodeInternal, targetId, updater)
        ),
      };
    }
    return node;
  };

  // 辅助函数：清除树中所有新建文件夹输入框
  const _clearNewFolderInputs = (nodes: FolderNodeInternal[]): FolderNodeInternal[] => {
    return nodes.map((n) => ({
      ...n,
      newFolderInput: false,
      newFolderValue: undefined,
      children: n.children ? _clearNewFolderInputs(n.children as FolderNodeInternal[]) : undefined,
    }));
  };

  const renderFolderTree = () => (
    <div className="py-2">
      {/* 全部文档选项 */}
      <div
        className={`flex items-center gap-2 px-4 py-1.5 rounded-md cursor-pointer transition-colors ${
          selectedFolderId === null ? "bg-sidebar-accent" : "hover:bg-sidebar-accent/50"
        }`}
        onClick={() => setSelectedFolderId(null)}
      >
        <div className="w-4" />
        <Folder className="w-4 h-4 text-muted-foreground" />
        <span className="text-sm">全部文献</span>
      </div>

      {/* 文件夹树 */}
      {folders.map((folder) => (
        <div key={folder.id} className="group">
          <FolderTreeItem
            node={folder}
            selectedFolderId={selectedFolderId}
            onSelectFolder={setSelectedFolderId}
            onRename={handleRename}
            onDelete={(id) => {
              const node = _findFolderNode(folders, id);
              setDeleteTarget({ type: "folder", id, name: node?.name ?? "该文件夹" });
            }}
            onCreateSubfolder={handleCreateSubfolder}
            onCancelNewFolder={handleCancelNewFolder}
            onConfirmNewFolder={handleConfirmNewFolder}
            onRenameSubmit={handleRenameSubmit}
            dragOverFolderId={dragOverFolderId}
            onDragOver={handleDragOver}
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          />
        </div>
      ))}

      {/* 根级别新建文件夹 */}
      {showRootNewFolder && (
        <div className="flex items-center gap-1 px-4 py-1.5">
          <div className="w-4" />
          <Folder className="w-4 h-4 text-muted-foreground flex-shrink-0" />
          <input
            autoFocus
            className="flex-1 text-sm bg-background border border-border rounded px-1 py-0.5"
            placeholder="文件夹名称"
            value={rootNewFolderValue}
            onChange={(e) => setRootNewFolderValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (e.currentTarget.value.trim()) {
                  handleConfirmNewFolder(null, e.currentTarget.value.trim());
                }
                setShowRootNewFolder(false);
                setRootNewFolderValue("");
              } else if (e.key === "Escape") {
                setShowRootNewFolder(false);
                setRootNewFolderValue("");
              }
            }}
            onBlur={(e) => {
              // 延迟读取 DOM 实时值（避免 blur 在 click 之前触发时 state 已过时）
              setTimeout(() => {
                const v = e.currentTarget.value;
                if (v.trim()) {
                  handleConfirmNewFolder(null, v.trim());
                }
                setShowRootNewFolder(false);
                setRootNewFolderValue("");
              }, 0);
            }}
          />
        </div>
      )}

      {/* 新建文件夹按钮 */}
      {!showRootNewFolder && (
        <button
          onClick={() => setShowRootNewFolder(true)}
          className="flex items-center gap-2 px-4 py-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <div className="w-4" />
          <FolderPlus className="w-4 h-4" />
          新建文件夹
        </button>
      )}
    </div>
  );

  return (
    <div className="flex h-full">
      {/* 左侧边栏 */}
      <div className="w-64 bg-[#eee8d5] border-r border-sidebar-border overflow-y-auto flex flex-col">
        <div className="flex-1 overflow-y-auto">{renderFolderTree()}</div>

        {/* 上传按钮 */}
        <div className="p-4 border-t border-sidebar-border bg-[#eee8d5]">
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf"
            multiple
            onChange={handleUpload}
            className="hidden"
            id="pdf-upload"
          />
          <label
            htmlFor="pdf-upload"
            className={`flex items-center gap-2 px-4 py-2 rounded-lg border border-border cursor-pointer hover:bg-sidebar-accent transition-colors text-sm ${
              isUploading ? "opacity-50 pointer-events-none" : ""
            }`}
          >
            {isUploading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Upload className="w-4 h-4" />
            )}
            <span>{isUploading ? "上传中..." : "上传 PDF"}</span>
          </label>
          {uploadStatus && (
            <div className="mt-2 text-xs text-muted-foreground">{uploadStatus}</div>
          )}
        </div>
      </div>

      {/* 右侧内容区 */}
      <div className="flex-1 overflow-y-auto bg-[#fefdf6]">
        <div className="max-w-5xl mx-auto px-12 py-8">
          {isLoading ? (
            <div className="flex items-center justify-center py-20 text-muted-foreground text-sm">
              加载文档库...
            </div>
          ) : error ? (
            <div className="flex items-center justify-center py-20 text-red-400 text-sm gap-2">
              <AlertCircle className="w-4 h-4" />
              {error}
            </div>
          ) : documents.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-muted-foreground text-sm gap-4">
              <p>暂无文档，请上传 PDF 开始处理</p>
            </div>
          ) : (
            <div className="space-y-0">
              {documents.map((doc, index) => (
                <div key={doc.id}>
                  <div
                    draggable
                    onDragStart={(e) => {
                      console.log("selectedDocIds:", selectedDocIds.size, Array.from(selectedDocIds));
                      if (selectedDocIds.size > 0 && selectedDocIds.has(doc.id)) {
                        e.dataTransfer.setData("paperIds", JSON.stringify(Array.from(selectedDocIds)));
                      } else {
                        e.dataTransfer.setData("paperId", doc.id);
                      }
                      e.dataTransfer.effectAllowed = "move";
                      setDraggedDocId(doc.id);
                    }}
                    onDragEnd={handleDragEnd}
                    className={`py-3 flex items-start justify-between gap-8 ${
                      draggedDocId === doc.id ? "opacity-50" : ""
                    }`}
                  >
                    <ContextMenu>
                      <ContextMenuTrigger asChild>
                        <div className="flex items-center flex-1 min-w-0 cursor-grab active:cursor-grabbing">
                          <input
                            type="checkbox"
                            className="mr-2 cursor-pointer"
                            checked={selectedDocIds.has(doc.id)}
                            onChange={() => toggleSelectDoc(doc.id)}
                            onClick={e => e.stopPropagation()}
                          />
                          <h3 className="text-foreground mb-1 text-[16px]">{doc.title}</h3>
                        </div>
                      </ContextMenuTrigger>
                      <ContextMenuContent>
                        {selectedDocIds.size > 1 && selectedDocIds.has(doc.id) && (
                          <ContextMenuItem
                            onClick={handleBatchDelete}
                            className="text-red-500 focus:text-red-500"
                          >
                            <Trash2 className="w-4 h-4 mr-2" />
                            删除选中的 {selectedDocIds.size} 条文献
                          </ContextMenuItem>
                        )}
                        <ContextMenuItem
                          onClick={() => {
                            if (deletingIds.has(doc.id)) return;
                            setDeleteTarget({ type: "document", id: doc.id, name: doc.title });
                          }}
                          className="text-red-500 focus:text-red-500"
                        >
                          <Trash2 className="w-4 h-4 mr-2" />
                          删除文献
                        </ContextMenuItem>
                      </ContextMenuContent>
                    </ContextMenu>

                    <div className="flex items-center gap-8 flex-shrink-0">
                      <span className="text-sm text-muted-foreground whitespace-nowrap">
                        {doc.addedDate}
                      </span>
                      <div className="w-5 flex items-center justify-center">
                        <StatusIcon status={doc.status} onRetry={() => handleRetry(doc.id)} onRetryDelete={() => handleRetryDelete(doc.id)} />
                      </div>
                    </div>
                  </div>
                  {index < documents.length - 1 && (
                    <div className="h-px bg-divider opacity-40 mx-4" />
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 删除确认对话框 */}
      <AlertDialog open={deleteTarget !== null} onOpenChange={() => setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              确认删除{deleteTarget?.type === "folder" ? "文件夹" : "文献"}？
            </AlertDialogTitle>
          </AlertDialogHeader>
          {deleteTarget?.type === "folder" ? (
            <AlertDialogDescription>
              删除后，该文件夹内的文档将移至「未归类」
            </AlertDialogDescription>
          ) : (
            <AlertDialogDescription>
              确定要删除「{deleteTarget?.name}」吗？此操作不可撤销。
            </AlertDialogDescription>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setDeleteTarget(null)}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={deleteTarget?.type === "folder" ? handleDeleteFolder : handleDeleteDocument}
              className="bg-red-500 hover:bg-red-600"
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Toast 通知（右下角固定） */}
      <div className="fixed bottom-6 right-6 flex flex-col gap-2 z-50 pointer-events-none">
        {toasts.map((toast: { id: string; message: string }) => (
          <div
            key={toast.id}
            className="bg-[#e5e5e5] text-black text-sm px-4 py-3 rounded shadow-md animate-toast-fade"
          >
            {toast.message}
          </div>
        ))}
      </div>
    </div>
  );
}

// 辅助函数：按 id 查找文件夹节点
function _findFolderNode(nodes: FolderNodeInternal[], id: string): FolderNodeInternal | null {
  for (const node of nodes) {
    if (node.id === id) return node;
    if (node.children) {
      const found = _findFolderNode(node.children as FolderNodeInternal[], id);
      if (found) return found;
    }
  }
  return null;
}
