import React, { useState, useCallback, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import ReactMarkdown from 'react-markdown';
import { PanelLeftClose, PanelLeftOpen, Search, History, Filter } from 'lucide-react';
import { fetchRetrieve, type RetrievedDocument } from '../api';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";
import { DocumentSelectionModal } from './DocumentSelectionModal';

interface OpinionSearchProps {}

/** 历史记录快照：包含查询词、结果、分析结论、TopK、时间戳 */
interface HistoryItem {
  id: string;
  query: string;
  results: RetrievedDocument[];
  analysis: string | null;
  analysis_sources: Record<string, unknown>[] | null;
  topK: number;
  timestamp: number;
}

export function OpinionSearch() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [hasSearched, setHasSearched] = useState(false);
  const [results, setResults] = useState<RetrievedDocument[]>([]);
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [analysisSources, setAnalysisSources] = useState<Record<string, unknown>[] | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [topK, setTopK] = useState<number>(5);
  const [history, setHistory] = useState<HistoryItem[]>([]);

  useEffect(() => {
    const apiBase = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
    fetch(`${apiBase}/api/history/opinion`)
      .then(r => r.json())
      .then((data: HistoryItem[]) => {
        setHistory(data);
        if (data.length > 0) setCurrentHistoryId(data[0].id);
      })
      .catch(() => {});
  }, []);
  /** 当前高亮的历史记录 ID */
  const [currentHistoryId, setCurrentHistoryId] = useState<string | null>(null);
  /** 文献范围筛选：paper_id 数组，[] 代表全选 */
  const [selectedScope, setSelectedScope] = useState<string[]>([]);
  const [scopeModalOpen, setScopeModalOpen] = useState(false);
  /** 输入区是否折叠为单行摘要条 */
  const [isInputCollapsed, setIsInputCollapsed] = useState(false);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    id: string;
  } | null>(null);

  const persistHistory = async (items: HistoryItem[]) => {
    const apiBase = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
    await fetch(`${apiBase}/api/history/opinion`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(items),
    });
    setHistory(items);
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setIsLoading(true);
    setError(null);
    setIsInputCollapsed(true);
    try {
      const { results: retrievedResults, analysis, analysis_sources } = await fetchRetrieve(
        searchQuery, topK, selectedScope.length > 0 ? selectedScope : undefined
      );
      setResults(retrievedResults);
      setAnalysis(analysis);
      setAnalysisSources(analysis_sources);
      setHasSearched(true);

      // 保存完整快照（query + results + analysis + topK + timestamp）
      const newItem: HistoryItem = {
        id: crypto.randomUUID(),
        query: searchQuery.trim(),
        results: retrievedResults,
        analysis,
        analysis_sources,
        topK,
        timestamp: Date.now(),
      };
      const deduped = [newItem, ...history.filter((h: HistoryItem) => h.query !== newItem.query)].slice(0, 30);
      persistHistory(deduped);
      setCurrentHistoryId(newItem.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "检索失败，请重试");
      setHasSearched(true);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSearch();
    }
  };

  /** 点击历史记录：同时恢复 query、results、analysis、topK */
  const handleHistoryClick = (item: HistoryItem) => {
    setSearchQuery(item.query);
    setResults(item.results);
    setAnalysis(item.analysis ?? null);
    setAnalysisSources(item.analysis_sources ?? null);
    setTopK(item.topK);
    setCurrentHistoryId(item.id);
    setHasSearched(true);
    setError(null);
    setIsInputCollapsed(false);
  };

  /** TopK 切换：向下兼容走前端 slice，向上补偿走网络重载 */
  const handleTopKChange = useCallback(async (newTopK: number) => {
    if (newTopK <= results.length) {
      // 向下兼容：前端直接 slice，不发请求
      setTopK(newTopK);
    } else if (searchQuery.trim()) {
      // 向上补偿：需要更多结果，发起网络重载（保留 selectedScope 筛选条件）
      setIsLoading(true);
      try {
        const { results: retrievedResults, analysis: newAnalysis, analysis_sources: newSources } = await fetchRetrieve(
          searchQuery,
          newTopK,
          selectedScope.length > 0 ? selectedScope : undefined
        );
        setResults(retrievedResults);
        setAnalysis(newAnalysis);
        setAnalysisSources(newSources);
        // 静默更新缓存中当前历史记录的 results、analysis、analysis_sources 和 topK
        if (currentHistoryId) {
          const updated = history.map((h: HistoryItem) =>
            h.id === currentHistoryId
              ? { ...h, results: retrievedResults, analysis: newAnalysis, analysis_sources: newSources, topK: newTopK }
              : h
          );
          persistHistory(updated);
        }
        setTopK(newTopK);
      } catch (err) {
        setError(err instanceof Error ? err.message : "检索失败，请重试");
      } finally {
        setIsLoading(false);
      }
    }
  }, [results.length, searchQuery, selectedScope, currentHistoryId]);

  const handleDeleteHistory = (id: string) => {
    const newHistory = history.filter((h: HistoryItem) => h.id !== id);
    persistHistory(newHistory);
    setContextMenu(null);
  };

  const handleContextMenu = (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, id });
  };

  /** 新建检索：保存当前状态（如有），然后重置工作区 */
  const handleNewSearch = () => {
    const hasContent = searchQuery.trim() || results.length > 0;
    if (hasContent) {
      const newItem: HistoryItem = {
        id: crypto.randomUUID(),
        query: searchQuery.trim(),
        results,
        analysis: analysis ?? null,
        analysis_sources: analysisSources ?? null,
        topK,
        timestamp: Date.now(),
      };
      // 追加到最前，去重（同 query 保留更新的那条），最多 30 条
      const deduped = [newItem, ...history.filter((h: HistoryItem) => h.query !== newItem.query)].slice(0, 30);
      persistHistory(deduped);
    }
    setSearchQuery("");
    setResults([]);
    setAnalysis(null);
    setAnalysisSources(null);
    setCurrentHistoryId(null);
    setHasSearched(false);
    setError(null);
    setIsInputCollapsed(false);
  };

  return (
    <div className="flex w-full h-full bg-white font-sans text-gray-900 overflow-hidden">
      {/* 左侧边栏 - 历史记录 */}
      <motion.div
        initial={false}
        animate={{ width: isSidebarOpen ? 320 : 64 }}
        className="h-full border-r border-gray-200/40 flex flex-col overflow-hidden shrink-0 bg-[#eee8d5] z-10 whitespace-nowrap"
      >
        {/* 边栏顶部 */}
        <div className="h-12 flex items-center px-4 border-b border-gray-200/40 text-gray-400">
          <AnimatePresence mode="wait">
            {isSidebarOpen && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex items-center gap-2 flex-1"
              >
                <span className="text-lg tracking-wider uppercase font-bold text-gray-500">历史搜索</span>
              </motion.div>
            )}
          </AnimatePresence>
          <button
            onClick={() => setIsSidebarOpen(!isSidebarOpen)}
            className="p-1.5 hover:bg-gray-50 rounded-md transition-colors text-gray-400 hover:text-gray-900 shrink-0 mx-auto"
            title={isSidebarOpen ? "收起历史记录" : "展开历史记录"}
          >
            {isSidebarOpen ? <PanelLeftClose size={18} strokeWidth={1.5} /> : <PanelLeftOpen size={18} strokeWidth={1.5} />}
          </button>
        </div>

        {/* 边栏内容区 */}
        <div className="flex-1 overflow-y-auto py-2">
          {isSidebarOpen ? (
            <div className="flex flex-col h-full">
              {/* 新建检索按钮 */}
              <button
                onClick={handleNewSearch}
                className="mx-4 my-2 py-2 text-[13px] text-muted-foreground hover:text-foreground rounded-lg hover:bg-gray-50 transition-colors bg-white"
              >
                + 新建检索
              </button>
              {history.length === 0 ? (
                <div className="px-3 py-4 text-[13px] text-gray-400 text-center">
                  暂无搜索历史
                </div>
              ) : (
                history.filter((item: HistoryItem) => item.id && item.query).map((item: HistoryItem) => (
                  <div
                    key={item.id}
                    onClick={() => handleHistoryClick(item)}
                    onContextMenu={(e) => handleContextMenu(e, item.id)}
                    className={`px-4 py-3 text-[13px] cursor-pointer transition-colors truncate ${
                      currentHistoryId === item.id
                        ? "bg-white text-foreground"
                        : "text-muted-foreground hover:bg-[#eee8d5] hover:text-foreground"
                    }`}
                  >
                    {item.query}
                  </div>
                ))
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center gap-4 py-4 opacity-50 text-gray-400">
              <History size={18} strokeWidth={1.5} />
            </div>
          )}
        </div>
      </motion.div>

      {/* 右侧主工作区 */}
      <div className="h-full flex-1 flex flex-col min-w-0 bg-white">
        {/* 顶部输入区 - 带平滑高度动画 */}
        <motion.div
          initial={false}
          animate={{
            height: isInputCollapsed ? '56px' : '35%',
          }}
          transition={{ duration: 0.3, ease: 'easeInOut' }}
          className="flex items-center justify-center border-b border-gray-200/40 relative bg-[#fefdf6] shrink-0 overflow-hidden px-8"
        >
          <div className="w-full max-w-3xl relative flex flex-col justify-center h-full">
            {/* 单个 textarea，高度动态变化 */}
            <textarea
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              onClick={() => setIsInputCollapsed(false)}
              onFocus={() => setIsInputCollapsed(false)}
              className={`w-full text-sm md:text-base lg:text-lg text-gray-800 placeholder:text-gray-300 placeholder:italic resize-none focus:outline-none bg-transparent leading-relaxed tracking-wide transition-all duration-300 ${
                isInputCollapsed
                  ? 'h-[40px] min-h-[40px] overflow-hidden whitespace-nowrap mt-0'
                  : 'min-h-[120px] mt-4'
              }`}
              placeholder="在此输入你的论点:"
            />

            {/* 操作按钮 - 仅展开时显示 */}
            <AnimatePresence>
              {!isInputCollapsed && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.3, ease: 'easeInOut' }}
                  className="flex justify-between items-end mt-4 overflow-hidden shrink-0"
                >
                  {/* 来源选择器（左下角）*/}
                  <button
                    onClick={() => setScopeModalOpen(true)}
                    className="flex items-center gap-1.5 px-4 py-1.5 rounded-full border border-gray-200/60 text-[13px] text-gray-500 hover:bg-gray-50 hover:text-gray-900 transition-colors bg-white shadow-sm"
                  >
                    {selectedScope.length === 0
                      ? "范围：全部文献"
                      : `范围：已选 ${selectedScope.length} 篇`}
                    <Filter size={14} strokeWidth={1.5} />
                  </button>

                  {/* 操作行：TopK 选择器 + 搜索按钮 */}
                  <div className="flex items-center gap-2">
                    {/* TopK 选择器 */}
                    <div className="flex items-center gap-1.5">
                      <span className="text-[13px] text-gray-400 whitespace-nowrap">返回数量</span>
                      <Select
                        value={String(topK)}
                        onValueChange={(v) => handleTopKChange(Number(v))}
                      >
                        <SelectTrigger className="h-8 w-[70px] text-[13px] border-gray-200/60 bg-white shadow-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {[3, 5, 10, 15, 20].map((n) => (
                            <SelectItem key={n} value={String(n)}>
                              {n}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    {/* 搜索按钮（右下角）*/}
                    <button
                      onClick={handleSearch}
                      disabled={!searchQuery.trim() || isLoading}
                      className="flex items-center gap-2 bg-gray-900 text-white px-6 py-2 rounded-full text-[13px] tracking-wider font-medium hover:bg-black transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-md shadow-gray-900/10"
                    >
                      {isLoading ? "检索中..." : "搜索"} <Search size={14} strokeWidth={2} />
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </motion.div>

        {/* 底部结果区 - 点击收起输入区 */}
        <div
          className="flex-1 overflow-y-auto bg-[#fefdf6] pb-20"
          onClick={() => setIsInputCollapsed(true)}
        >
          <div className="max-w-4xl mx-auto px-10 pt-4">
            {!hasSearched ? (
              <div className="flex items-center justify-center h-full min-h-[300px] text-gray-300 text-sm tracking-wide">
                <span className="opacity-100">
                  {isLoading ? "正在全力检索中......" : "等待用户输入论点ing..."}
                </span>
              </div>
            ) : error ? (
              <div className="flex items-center justify-center h-full min-h-[300px] text-red-400 text-sm">
                {error}
              </div>
            ) : (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4 }}
                className="flex flex-col"
              >
                {/* LLM 观点分析卡片 */}
                {analysis && !isLoading && (
                  <div className="mb-6 p-5 bg-gray-50/80 border border-gray-200/60 rounded-xl font-semibold">
                    <ReactMarkdown>{analysis}</ReactMarkdown>
                  </div>
                )}

                {(results as RetrievedDocument[]).slice(0, topK).map((result: RetrievedDocument, idx: number, arr: RetrievedDocument[]) => (
                  <div
                    key={`${result.id}-${idx}`}
                    className={`py-8 relative group ${idx !== arr.length - 1 ? 'border-b border-gray-200/40' : ''}`}
                  >
                    {/* 元数据行 */}
                    <div className="text-[12px] text-gray-400 mb-3 font-medium tracking-wide">
                      [来源: {result.source} | 相似度: {result.similarity.toFixed(2)}]
                    </div>

                    {/* 原文 */}
                    <p className="text-gray-700 text-[15px] leading-[1.8] tracking-[0.015em] font-light">
                      {result.text}
                    </p>
                  </div>
                ))}
              </motion.div>
            )}
          </div>
        </div>
      </div>

      {/* 右键菜单 */}
      {contextMenu && (
        <div
          className="fixed z-50 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[120px]"
          style={{ top: contextMenu.y, left: contextMenu.x }}
        >
          <button
            onClick={() => handleDeleteHistory(contextMenu.id)}
            className="w-full px-4 py-2 text-[13px] text-red-500 hover:bg-gray-50 text-left transition-colors"
          >
            删除
          </button>
        </div>
      )}
      {/* 点击空白关闭右键菜单 */}
      {contextMenu && (
        <div
          className="fixed inset-0 z-40"
          onClick={() => setContextMenu(null)}
        />
      )}

      {/* 文献范围筛选弹窗 */}
      <DocumentSelectionModal
        isOpen={scopeModalOpen}
        onClose={() => setScopeModalOpen(false)}
        initialSelectedIds={selectedScope}
        onConfirm={(ids) => setSelectedScope(ids)}
      />
    </div>
  );
}
