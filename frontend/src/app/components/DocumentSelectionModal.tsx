import { useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
} from "./ui/dialog";
import { Checkbox } from "./ui/checkbox";
import { Button } from "./ui/button";
import { useDocumentTree } from "../context/DocumentTreeContext";
import {
  ChevronDown,
  ChevronRight,
  Folder,
  FolderOpen,
  FileText,
} from "lucide-react";
import { cn } from "./ui/utils";

interface DocumentSelectionModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** 传入的 paper_id 数组；[] 代表全选 */
  initialSelectedIds: string[];
  onConfirm: (selectedIds: string[]) => void;
}

/** 弹窗内部树节点 */
interface ModalTreeNode {
  id: string;
  name: string;
  type: "folder" | "file";
  children?: ModalTreeNode[];
  /** 仅 file 节点有 */
  filename?: string;
  status?: "processing" | "ready";
}

function buildModalTree(
  folders: import("../api").FolderNode[],
  documents: import("../api").DocumentRecord[]
): ModalTreeNode[] {
  const mapFolder = (f: import("../api").FolderNode): ModalTreeNode => {
    const childFolderNodes = (f.children || []).map(mapFolder);
    const fileNodes: ModalTreeNode[] = documents
      .filter((d) => d.folder_id === f.id)
      .map((d) => ({
        id: d.id,
        name: d.title,
        type: "file" as const,
        status: d.status,
      }));
    const tn: ModalTreeNode = {
      id: f.id,
      name: f.name,
      type: "folder",
      children: [...childFolderNodes, ...fileNodes],
    };
    return tn;
  };

  return folders.map(mapFolder);
}

/** 递归收集节点下所有 ready 文件的 filename */
function collectReadyFilenames(node: ModalTreeNode): string[] {
  if (node.type === "file") {
    return node.status === "ready" && node.filename ? [node.filename] : [];
  }
  return (node.children || []).flatMap(collectReadyFilenames);
}

/** 计算文件夹节点的半选/全选状态 */
function calcChecked(node: ModalTreeNode, selectedIds: Set<string>): boolean | "indeterminate" {
  if (node.type === "file") {
    if (node.status === "processing") return false;
    return selectedIds.has(node.id) ? true : false;
  }

  const readyChildren = (node.children || []).filter(
    (c) => c.type === "file" && c.status === "ready"
  );
  if (readyChildren.length === 0) return false;

  const selectedCount = readyChildren.filter((c) => selectedIds.has(c.id)).length;
  if (selectedCount === 0) return false;
  if (selectedCount === readyChildren.length) return true;
  return "indeterminate";
}

function ModalTreeNode({
  node,
  selectedIds,
  onToggle,
}: {
  node: ModalTreeNode;
  selectedIds: Set<string>;
  onToggle: (node: ModalTreeNode) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const isFolder = node.type === "folder";
  const isProcessing = node.type === "file" && node.status === "processing";
  const checked = isFolder
    ? calcChecked(node, selectedIds)
    : isProcessing
    ? false
    : selectedIds.has(node.id);

  return (
    <div>
      <div
        className={cn(
          "flex items-center gap-2 px-4 py-2 hover:bg-[#EDF2F9] transition-colors",
          checked === true && !isProcessing ? "bg-[#EDF2F9]" : "",
          isProcessing ? "opacity-50" : ""
        )}
      >
        {isFolder ? (
          <button onClick={() => setExpanded(!expanded)} className="flex-shrink-0">
            {expanded ? (
              <ChevronDown className="w-4 h-4 text-muted-foreground" />
            ) : (
              <ChevronRight className="w-4 h-4 text-muted-foreground" />
            )}
          </button>
        ) : (
          <div className="w-4 flex-shrink-0" />
        )}

        <Checkbox
          checked={checked}
          disabled={isProcessing}
          onCheckedChange={() => !isProcessing && onToggle(node)}
          className="flex-shrink-0"
        />

        {isFolder ? (
          expanded ? (
            <FolderOpen className="w-4 h-4 text-muted-foreground flex-shrink-0" />
          ) : (
            <Folder className="w-4 h-4 text-muted-foreground flex-shrink-0" />
          )
        ) : (
          <FileText className="w-4 h-4 text-muted-foreground flex-shrink-0" />
        )}

        <span className={cn("truncate text-[15px]", isProcessing ? "text-muted-foreground" : "")}>
          {node.name}
        </span>
      </div>

      {expanded && isFolder && node.children && node.children.length > 0 && (
        <div className="ml-4">
          {node.children.map((child) => (
            <ModalTreeNode
              key={child.id}
              node={child}
              selectedIds={selectedIds}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function DocumentSelectionModal({
  isOpen,
  onClose,
  initialSelectedIds,
  onConfirm,
}: DocumentSelectionModalProps) {
  const { folders, documents } = useDocumentTree();

  /** 仅 ready 状态的文件 */
  const readyFiles = useMemo(
    () => documents.filter((d) => d.status === "ready"),
    [documents]
  );

  const allReadyIds = useMemo(
    () => new Set(readyFiles.map((f) => f.id)),
    [readyFiles]
  );

  /**
   * localSelectedIds 语义：
   * - 内部始终存具体 ID 集合（never null）
   * - 初始化时用空集占位（等 useEffect 同步真实值），避免 allReadyIds 时机问题
   */
  const [localSelectedIds, setLocalSelectedIds] = useState<Set<string>>(new Set());

  /** initialized：标记 useEffect 是否已完成首次同步 */
  const [initialized, setInitialized] = useState(false);

  /** 每次弹窗打开时同步一次；关闭时重置为空集和未初始化状态 */
  useEffect(() => {
    if (isOpen) {
      if (initialSelectedIds.length === 0) {
        setLocalSelectedIds(new Set(allReadyIds));
      } else {
        setLocalSelectedIds(new Set(initialSelectedIds));
      }
      setInitialized(true);
    } else {
      setLocalSelectedIds(new Set());
      setInitialized(false);
    }
  }, [isOpen, initialSelectedIds, allReadyIds]);

  /** 构建弹窗内部树 */
  const tree = useMemo(() => buildModalTree(folders, documents), [folders, documents]);

  /**
   * folderToFilesMap：递归收集每个文件夹下所有 ready 文件的 ID
   * 键为 folder_id，值为包含自身及所有子孙文件夹下 ready 文件的 ID 集合
   */
  const folderToFilesMap = useMemo(() => {
    const map: Record<string, Set<string>> = {};

    const collect = (node: ModalTreeNode): Set<string> => {
      const fileIds = new Set<string>();
      if (node.type === "file" && node.status === "ready") {
        fileIds.add(node.id);
      }
      for (const child of node.children || []) {
        const childIds = collect(child);
        childIds.forEach((id) => fileIds.add(id));
      }
      map[node.id] = fileIds;
      return fileIds;
    };

    for (const root of tree) {
      collect(root);
    }
    return map;
  }, [tree]);

  const handleToggle = (node: ModalTreeNode) => {
    setLocalSelectedIds((prev) => {
      const next = new Set(prev);
      if (node.type === "folder") {
        const ids = folderToFilesMap[node.id] || new Set();
        const allSelected = Array.from(ids).every((id) => next.has(id));
        if (allSelected) {
          ids.forEach((id) => next.delete(id));
        } else {
          ids.forEach((id) => next.add(id));
        }
      } else {
        if (next.has(node.id)) {
          next.delete(node.id);
        } else {
          next.add(node.id);
        }
      }
      return next;
    });
  };

  const allSelected = allReadyIds.size > 0 && allReadyIds.size === localSelectedIds.size;

  const handleSelectAll = () => {
    if (allSelected) {
      setLocalSelectedIds(new Set());
    } else {
      setLocalSelectedIds(new Set(allReadyIds));
    }
  };

  const handleConfirm = () => {
    // 契约：selectedIds.length === readyFiles.length → 传递 []（全选）
    if (localSelectedIds.size === readyFiles.length) {
      onConfirm([]);
    } else {
      onConfirm(Array.from(localSelectedIds));
    }
    setTimeout(() => {
      (document.activeElement as HTMLElement | null)?.blur?.();
    }, 0);
    onClose();
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-3xl max-h-[100vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>选择文献范围</DialogTitle>
        </DialogHeader>

        {/* 全选按钮 */}
        <div className="px-4 py-2 border-b border-gray-100">
          <label className="flex items-center gap-2 cursor-pointer">
            <Checkbox checked={allSelected} onCheckedChange={handleSelectAll} />
            <span className="text-sm font-medium">全选所有来源</span>
          </label>
        </div>

        {/* 树形结构 */}
        <div className="flex-1 overflow-y-auto py-2">
          {tree.length === 0 ? (
            <div className="text-sm text-muted-foreground text-center py-8">
              暂无文献
            </div>
          ) : !initialized ? (
            // initialized 为 false 时表示弹窗刚打开，useEffect 尚未完成同步
            <div className="text-sm text-muted-foreground text-center py-8">
              加载中...
            </div>
          ) : (
            tree.map((node) => (
              <ModalTreeNode
                key={node.id}
                node={node}
                selectedIds={localSelectedIds}
                onToggle={handleToggle}
              />
            ))
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={handleConfirm}>
            确认
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
