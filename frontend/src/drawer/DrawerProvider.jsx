import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from 'react';

/* Global state för pipeline-drawer. En rad kan vara vald utan att drawern
 * är öppen — topbar- och sidebar-pipeline-ikonerna visar rad-valet
 * visuellt och öppnar drawern när de klickas. */

const DrawerContext = createContext(null);

const VALID_TABS = ['gmail', 'ai', 'drive', 'bezala'];

export function DrawerProvider({ children }) {
  const [selectedMessage, setSelectedMessage] = useState(null);
  const [activeTab, setActiveTab] = useState('gmail');
  const [isOpen, setIsOpen] = useState(false);

  const selectMessage = useCallback((message) => {
    setSelectedMessage(message);
  }, []);

  const openDrawer = useCallback((message, tab = 'gmail') => {
    if (message) setSelectedMessage(message);
    if (VALID_TABS.includes(tab)) setActiveTab(tab);
    setIsOpen(true);
  }, []);

  const closeDrawer = useCallback(() => {
    setIsOpen(false);
  }, []);

  const setTab = useCallback((tab) => {
    if (VALID_TABS.includes(tab)) setActiveTab(tab);
  }, []);

  const closeIfFor = useCallback((messageId) => {
    setIsOpen((open) => {
      if (!open) return open;
      return selectedMessage?.id === messageId ? false : open;
    });
  }, [selectedMessage]);

  const value = useMemo(
    () => ({
      selectedMessage,
      activeTab,
      isOpen,
      selectMessage,
      openDrawer,
      closeDrawer,
      setTab,
      closeIfFor,
    }),
    [
      selectedMessage,
      activeTab,
      isOpen,
      selectMessage,
      openDrawer,
      closeDrawer,
      setTab,
      closeIfFor,
    ],
  );

  return <DrawerContext.Provider value={value}>{children}</DrawerContext.Provider>;
}

export function useDrawer() {
  const ctx = useContext(DrawerContext);
  if (!ctx) throw new Error('useDrawer måste användas inuti <DrawerProvider>');
  return ctx;
}

export const DRAWER_TABS = VALID_TABS;
