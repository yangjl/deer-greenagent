"use client";

import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type FilesPanelContextValue = {
  open: boolean;
  setOpen: (open: boolean) => void;
};

const FilesPanelContext = createContext<FilesPanelContextValue | null>(null);

export function FilesPanelProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const value = useMemo(() => ({ open, setOpen }), [open]);
  return (
    <FilesPanelContext.Provider value={value}>
      {children}
    </FilesPanelContext.Provider>
  );
}

export function useMaybeFilesPanel() {
  return useContext(FilesPanelContext);
}

export function useFilesPanel() {
  const context = useContext(FilesPanelContext);
  if (!context) {
    throw new Error("useFilesPanel must be used within a FilesPanelProvider");
  }
  return context;
}
