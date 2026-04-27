import { createContext, useContext, useEffect, useState } from "react";
import { fetchDocuments, type FolderNode, type DocumentRecord } from "../api";

interface DocumentTreeContextValue {
  folders: FolderNode[];
  documents: DocumentRecord[];
  isLoading: boolean;
  refresh: () => void;
}

const DocumentTreeContext = createContext<DocumentTreeContextValue>({
  folders: [],
  documents: [],
  isLoading: false,
  refresh: () => {},
});

export function DocumentTreeProvider({ children }: { children: React.ReactNode }) {
  const [folders, setFolders] = useState<FolderNode[]>([]);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);

  const refresh = () => setRefreshToken((t) => t + 1);

  useEffect(() => {
    setIsLoading(true);
    fetchDocuments()
      .then((data) => {
        setFolders(data.folders);
        setDocuments(data.documents);
      })
      .catch((e) => {
        console.error("DocumentTreeContext: failed to load", e);
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, [refreshToken]);

  return (
    <DocumentTreeContext.Provider value={{ folders, documents, isLoading, refresh }}>
      {children}
    </DocumentTreeContext.Provider>
  );
}

export function useDocumentTree(): DocumentTreeContextValue {
  return useContext(DocumentTreeContext);
}
