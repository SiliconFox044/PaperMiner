import { useState, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import { Send, ChevronDown, ChevronUp, PanelLeftClose, PanelLeftOpen, Filter } from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { DocumentSelectionModal } from "./DocumentSelectionModal";
import { type Source } from "../api";

interface Message {
  id: string;
  type: "user" | "assistant";
  content: string;
  sources?: Source[];
}

interface Session {
  id: string;
  title: string;
  createdAt: number;
  messages: Message[];
  isEditing?: boolean;
}

interface KnowledgeQAProps {}

function SourcePanel({ sources }: { sources: Source[] }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mt-6">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        {expanded ? (
          <ChevronUp className="w-4 h-4" />
        ) : (
          <ChevronDown className="w-4 h-4" />
        )}
        <span className="bg-[#F5F5F5] rounded-[6px] px-2 py-0.5">{expanded ? "收起" : "展开"}参考上下文</span>
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: "easeInOut" }}
            className="overflow-hidden"
          >
            <div className="mt-4 space-y-4">
              {sources.map((source, index) => (
                <div key={index} className="border-l-2 border-divider pl-4 opacity-60">
                  <div className="text-sm mb-1">
                    <span className="text-foreground">{source.file}</span>
                    <span className="text-muted-foreground ml-2">· {source.path}</span>
                  </div>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {source.excerpt}
                  </p>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function KnowledgeQA() {
  const [input, setInput] = useState("");
  /** 文献范围筛选：paper_id 数组，[] 代表全选 */
  const [selectedScope, setSelectedScope] = useState<string[]>([]);
  const [scopeModalOpen, setScopeModalOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // ── 输入框自动高度 ─────────────────────────────────────────────────────────
  const adjustHeight = () => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };

  // ── 会话状态 ───────────────────────────────────────────────────────────────
  const [sessions, setSessions] = useState<Session[]>([]);
  const apiBase = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

  useEffect(() => {
    fetch(`${apiBase}/api/history/qa`)
      .then(r => r.json())
      .then((data: Session[]) => setSessions(data))
      .catch(() => {});
  }, [apiBase]);

  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${apiBase}/api/history/qa/current`)
      .then(r => r.json())
      .then((data: { id: string | null }) => setCurrentSessionId(data.id))
      .catch(() => {});
  }, [apiBase]);

  const [sessionContextMenu, setSessionContextMenu] = useState<{
    x: number;
    y: number;
    sessionId: string;
  } | null>(null);

  // 从 sessions 派生当前会话和消息列表
  const currentSession = sessions.find((s: Session) => s.id === currentSessionId) ?? null;
  const messages = currentSession?.messages ?? [];

  // ── 会话管理 ─────────────────────────────────────────────────────────────
  const persistSessions = (updater: Session[] | ((prev: Session[]) => Session[])) => {
    setSessions((prev) => {
      const newSessions = typeof updater === "function" ? (updater as (p: Session[]) => Session[])(prev) : updater;
      fetch(`${apiBase}/api/history/qa`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newSessions),
      }).catch(e => console.error("Failed to persist sessions:", e));
      return newSessions;
    });
  };

  const handleNewSession = () => {
    const newSession: Session = {
      id: Date.now().toString(),
      title: "新对话",
      createdAt: Date.now(),
      messages: [],
    };
    const updated = [newSession, ...sessions];
    persistSessions(updated);
    setCurrentSessionId(newSession.id);
    fetch(`${apiBase}/api/history/qa/current`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: newSession.id }),
    });
  };

  const handleSwitchSession = (id: string) => {
    setCurrentSessionId(id);
    fetch(`${apiBase}/api/history/qa/current`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    setSessionContextMenu(null);
  };

  const handleDeleteSession = (id: string) => {
    const updated = sessions.filter((s: Session) => s.id !== id);
    persistSessions(updated);
    if (currentSessionId === id) {
      const next = updated[0]?.id ?? null;
      setCurrentSessionId(next);
      fetch(`${apiBase}/api/history/qa/current`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: next }),
      });
    }
    setSessionContextMenu(null);
  };

  const handleRenameSession = (id: string) => {
    setSessions((prev: Session[]) => prev.map((s: Session) =>
      s.id === id ? { ...s, isEditing: true } : { ...s, isEditing: false }
    ));
    setSessionContextMenu(null);
  };

  const handleRenameSessionSubmit = (id: string, newTitle: string) => {
    if (!newTitle.trim()) return;
    const updated = sessions.map((s: Session) =>
      s.id === id ? { ...s, title: newTitle.trim(), isEditing: false } : s
    );
    persistSessions(updated);
  };

  // ── 发送消息 ──────────────────────────────────────────────────────────────
  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    let activeSessionId = currentSessionId;
    let activeSessions = sessions;

    if (!activeSessionId || !activeSessions.find((s: Session) => s.id === activeSessionId)) {
      const newSession: Session = {
        id: Date.now().toString(),
        title: "新对话",
        createdAt: Date.now(),
        messages: [],
      };
      activeSessions = [newSession, ...sessions];
      activeSessionId = newSession.id;
      setSessions(activeSessions);
      setCurrentSessionId(activeSessionId);
      fetch(`${apiBase}/api/history/qa/current`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: activeSessionId }),
      });
    }

    const userMessage: Message = {
      id: Date.now().toString(),
      type: "user",
      content: input,
    };

    // 追加 user message，同步 title
    const withUser = activeSessions.map((s: Session) => {
      if (s.id !== activeSessionId) return s;
      const newMessages = [...s.messages, userMessage];
      const title = s.title === "新对话" && newMessages.length === 1
        ? input.slice(0, 20)
        : s.title;
      return { ...s, messages: newMessages, title };
    });
    persistSessions(withUser);

    setInput("");
    if (inputRef.current) inputRef.current.style.height = "56px";
    setIsLoading(true);

    // 占位 assistant 消息，用于流式追加内容
    const assistantId = (Date.now() + 1).toString();
    const placeholderMessage: Message = {
      id: assistantId,
      type: "assistant",
      content: "",
      sources: [],
    };

    const withPlaceholder = withUser.map((s: Session) => {
      if (s.id !== activeSessionId) return s;
      return { ...s, messages: [...s.messages, placeholderMessage] };
    });
    setSessions(withPlaceholder);

    try {
      const res = await fetch(`${apiBase}/api/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: userMessage.content,
          paper_ids: selectedScope.length > 0 ? selectedScope : undefined,
          mode: "qa",
        }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data = await res.json();
      const answerText = data.answer ?? "";
      const answerSources: Source[] = data.sources ?? [];

      persistSessions((prev: Session[]) =>
        prev.map((s: Session) => {
          if (s.id !== activeSessionId) return s;
          return {
            ...s,
            messages: s.messages.map((m: Message) =>
              m.id === assistantId
                ? { ...m, content: answerText, sources: answerSources }
                : m
            ),
          };
        })
      );
    } catch (err) {
      const errorMessage: Message = {
        id: (Date.now() + 2).toString(),
        type: "assistant",
        content: `出错：${err instanceof Error ? err.message : "未知错误"}`,
      };
      persistSessions((prev: Session[]) =>
        prev.map((s: Session) => {
          if (s.id !== activeSessionId) return s;
          return {
            ...s,
            messages: [
              ...s.messages.filter((m: Message) => m.id !== assistantId),
              errorMessage,
            ],
          };
        })
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="h-full flex">
      {/* 左侧边栏 - 会话列表 */}
      <motion.div
        initial={false}
        animate={{ width: sidebarOpen ? 320 : 64 }}
        className="bg-[#eee8d5] border-r border-sidebar-border flex flex-col overflow-hidden whitespace-nowrap"
      >
        {/* 左侧顶部：仅会话 Tab */}
        <div className="h-12 flex items-center px-4 border-b border-sidebar-border">
          {sidebarOpen && (
            <span className="flex-1 text-lg tracking-wider uppercase font-bold text-gray-500">
              历史会话
            </span>
          )}
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-1.5 hover:bg-gray-50 rounded-md transition-colors text-gray-400 hover:text-gray-900 shrink-0 mx-auto"
          >
            {sidebarOpen ? (
              <PanelLeftClose size={18} strokeWidth={1.5} />
            ) : (
              <PanelLeftOpen size={18} strokeWidth={1.5} />
            )}
          </button>
        </div>

        {/* 左侧内容区：仅会话列表 */}
        <div className="flex-1 overflow-y-auto">
          {sidebarOpen && (
            <div className="flex flex-col h-full">
              {/* 新建对话按钮 */}
              <button
                onClick={handleNewSession}
                className="mx-4 my-2 py-2 text-[13px] text-muted-foreground hover:text-foreground rounded-lg hover:bg-gray-50 transition-colors bg-white"
              >
                + 新建对话
              </button>
              {/* 会话列表 */}
              <div className="flex-1 overflow-y-auto">
                {sessions.length === 0 ? (
                  <div className="px-4 py-6 text-[13px] text-muted-foreground text-center">
                    暂无对话历史
                  </div>
                ) : (
                  sessions.map((session: Session) => (
                    <div
                      key={session.id}
                      onClick={() => !session.isEditing && handleSwitchSession(session.id)}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        setSessionContextMenu({ x: e.clientX, y: e.clientY, sessionId: session.id });
                      }}
                      className={`px-4 py-3 text-[13px] cursor-pointer transition-colors ${
                        session.id === currentSessionId
                          ? "bg-sidebar-accent text-foreground"
                          : "text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
                      }`}
                    >
                      {session.isEditing ? (
                        <input
                          autoFocus
                          defaultValue={session.title}
                          className="w-full text-[13px] bg-background border border-border rounded px-1 py-0.5 focus:outline-none"
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              handleRenameSessionSubmit(session.id, e.currentTarget.value);
                            } else if (e.key === "Escape") {
                              setSessions((prev: Session[]) => prev.map((s: Session) =>
                                s.id === session.id ? { ...s, isEditing: false } : s
                              ));
                            }
                          }}
                          onBlur={(e) => {
                            handleRenameSessionSubmit(session.id, e.currentTarget.value);
                          }}
                          onClick={(e) => e.stopPropagation()}
                        />
                      ) : (
                        <span className="truncate block">{session.title}</span>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </motion.div>

      {/* 右侧 - 聊天区域 */}
      <div className="flex-1 flex flex-col">
        <div className="flex-1 overflow-y-auto bg-[#fefdf6]">
          <div className="max-w-3xl mx-auto px-12 py-12">
            {messages.length === 0 ? (
              <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
                输入问题，答案马上就到...
              </div>
            ) : (
              <div className="space-y-12">
                {messages.map((message) => (
                  <div key={message.id}>
                    {message.type === "user" ? (
                      <div className="text-foreground opacity-60 mb-8">{message.content}</div>
                    ) : (
                      <div>
                        <div className="text-foreground whitespace-pre-line leading-relaxed">
                          <ReactMarkdown>{message.content}</ReactMarkdown>
                        </div>
                        {message.sources && message.sources.length > 0 && (
                          <SourcePanel sources={message.sources} />
                        )}
                      </div>
                    )}
                  </div>
                ))}
                {isLoading && (
                  <div className="text-muted-foreground text-sm animate-pulse">
                    法律分析中...
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="border-t border-border py-6 bg-[#fefdf6]">
          <div className="flex items-center gap-4 px-8">
            {/* 范围指示器 */}
            <button
              onClick={() => setScopeModalOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1 rounded-full border border-gray-200/60 text-[12px] text-gray-500 hover:bg-gray-50 hover:text-gray-900 transition-colors bg-white shadow-sm shrink-0"
            >
              {selectedScope.length === 0
                ? "范围：全部文献"
                : `范围：已选 ${selectedScope.length} 篇`}
              <Filter size={12} strokeWidth={1.5} />
            </button>

            <div className="relative flex-1">
              <textarea
                ref={inputRef}
                rows={1}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  adjustHeight();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (!isLoading) handleSend();
                  }
                }}
                placeholder="输入您的问题..."
                className="w-full px-10 py-4 pr-14 bg-background border border-border rounded-full focus:outline-none focus:ring-2 focus:ring-ring shadow-lg resize-none overflow-y-auto leading-relaxed [&::-webkit-scrollbar]:hidden"
                style={{ height: "56px", minHeight: "56px", maxHeight: "120px", scrollbarWidth: "none", msOverflowStyle: "none" }}
              />
              <button
                onClick={handleSend}
                disabled={!input.trim() || isLoading}
                className="absolute right-3 top-1/2 -translate-y-1/2 p-2 rounded-full hover:bg-secondary transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <Send className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* 会话右键菜单 */}
      {sessionContextMenu && (
        <div
          className="fixed z-50 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[120px]"
          style={{ top: sessionContextMenu.y, left: sessionContextMenu.x }}
        >
          <button
            onClick={() => handleRenameSession(sessionContextMenu.sessionId)}
            className="w-full px-4 py-2 text-[13px] text-gray-700 hover:bg-gray-50 text-left transition-colors"
          >
            重命名
          </button>
          <button
            onClick={() => handleDeleteSession(sessionContextMenu.sessionId)}
            className="w-full px-4 py-2 text-[13px] text-red-500 hover:bg-gray-50 text-left transition-colors"
          >
            删除
          </button>
        </div>
      )}
      {sessionContextMenu && (
        <div
          className="fixed inset-0 z-40"
          onClick={() => setSessionContextMenu(null)}
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
