import { createContext, useContext, useEffect, useState } from "react";
import { fetchDocuments, type FolderNode, type DocumentRecord } from "../api";

interface DocumentTreeContextValue {
  folders: FolderNode[];
  documents: DocumentRecord[];
  isLoading: boolean;
}

const DocumentTreeContext = createContext<DocumentTreeContextValue>({
  folders: [],
  documents: [],
  isLoading: false,
});

export function DocumentTreeProvider({ children }: { children: React.ReactNode }) {
  const [folders, setFolders] = useState<FolderNode[]>([]);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [isLoading, setIsLoading] = useState(false);

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
  }, []);

  return (
    <DocumentTreeContext.Provider value={{ folders, documents, isLoading }}>
      {children}
    </DocumentTreeContext.Provider>
  );
}

export function useDocumentTree(): DocumentTreeContextValue {
  return useContext(DocumentTreeContext);
}
