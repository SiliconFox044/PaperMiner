import { useState, useCallback, useEffect } from 'react';
import { motion } from 'motion/react';
import { Minus } from 'lucide-react';
import { DocumentLibrary } from './components/document-library';
import { KnowledgeQA } from './components/knowledge-qa';
import { SegmentedControl } from './components/segmented-control';
import { OpinionSearch } from './components/OpinionSearch';
import { fetchDocuments, type FolderNode, type DocumentRecord } from './api';
import { DocumentTreeProvider } from './context/DocumentTreeContext';

type Tab = "library" | "opinion" | "qa";

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("library");
  const [folderTree, setFolderTree] = useState<FolderNode[]>([]);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [isNavExpanded, setIsNavExpanded] = useState(true);

  const loadFolderTree = useCallback(async () => {
    try {
      const data = await fetchDocuments();
      setFolderTree(data.folders);
      setDocuments(data.documents);
    } catch (e) {
      console.error("App: failed to load folder tree", e);
    }
  }, []);

  useEffect(() => {
    loadFolderTree();
  }, [loadFolderTree]);

  return (
    <DocumentTreeProvider>
      <div className="h-screen w-full bg-background font-sans text-foreground flex flex-col overflow-hidden selection:bg-black/5">
        {/* 顶部导航栏 */}
        <motion.div
          initial={false}
          animate={{
            height: isNavExpanded ? '74px' : '0px',
          }}
          transition={{ duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
          className="relative z-50 flex-shrink-0 bg-[#fdf6e3] shadow-[0_1px_2px_rgba(0,0,0,0.01)]"
        >
          <div className="absolute inset-0 overflow-hidden border-b border-black/[0.04] bg-[#fdf6e3]">
            <SegmentedControl value={activeTab} onChange={setActiveTab} />
          </div>

          {/* 底部居中的折叠把手 */}
          <button
            onClick={() => setIsNavExpanded(!isNavExpanded)}
            className="absolute -bottom-[20px] left-1/2 -translate-x-1/2 w-12 h-5 bg-white border-b border-l border-r border-black/[0.04] rounded-b-xl flex items-center justify-center hover:bg-gray-50 transition-colors shadow-sm cursor-pointer group z-10"
            title={isNavExpanded ? "收起导航栏" : "展开导航栏"}
          >
            <Minus
              size={16}
              strokeWidth={3}
              className="text-gray-300 group-hover:text-gray-500 transition-colors"
            />
          </button>
        </motion.div>

        {/* 主模块区域 */}
        <main className="flex-1 relative overflow-hidden bg-white">
          <div className={activeTab === "library" ? "absolute inset-0" : "hidden"}>
            <DocumentLibrary
              folderTree={folderTree}
              onFolderTreeChange={loadFolderTree}
            />
          </div>
          <div className={activeTab === "opinion" ? "absolute inset-0" : "hidden"}>
            <OpinionSearch />
          </div>
          <div className={activeTab === "qa" ? "absolute inset-0" : "hidden"}>
            <KnowledgeQA />
          </div>
        </main>
      </div>
    </DocumentTreeProvider>
  );

}
