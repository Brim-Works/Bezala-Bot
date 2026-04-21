import { useEffect, useRef } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { useDrawer } from './DrawerProvider.jsx';
import DrawerTabs from './DrawerTabs.jsx';
import GmailTab from './GmailTab.jsx';
import AiTab from './AiTab.jsx';
import DriveTab from './DriveTab.jsx';
import BezalaTab from './BezalaTab.jsx';
import { IconMail } from '../icons/index.jsx';

function stepStatusMap(message) {
  if (!message) return {};
  const ok = 'ok';
  const err = 'err';
  const idle = 'idle';
  const fileStatus = message.file_status || 'saved';
  return {
    gmail: ok,
    ai: message.ai_confidence != null ? ok : idle,
    drive: fileStatus === 'saved' ? ok : fileStatus === 'error' ? err : idle,
    bezala:
      message.bezala_status === 'transferred'
        ? ok
        : message.bezala_status === 'error'
          ? err
          : idle,
  };
}

/* Focus-trap är enkel: vi fokuserar close-knappen när drawern öppnas
 * och returnerar fokus till trigger-elementet vid stängning (tack vare
 * React:s default-beteende). */
export default function PipelineDrawer({ onRefetch }) {
  const { t } = useI18n();
  const { selectedMessage, activeTab, isOpen, setTab, closeDrawer } = useDrawer();
  const closeBtnRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return undefined;
    function onKey(e) {
      if (e.key === 'Escape') closeDrawer();
    }
    window.addEventListener('keydown', onKey);
    // Flytta fokus till close-knappen för tillgänglighet
    const t0 = setTimeout(() => closeBtnRef.current?.focus(), 50);
    // Låt body inte scrolla när drawern är öppen
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      clearTimeout(t0);
      document.body.style.overflow = prev;
    };
  }, [isOpen, closeDrawer]);

  if (!isOpen || !selectedMessage) return null;

  const statusMap = stepStatusMap(selectedMessage);

  return (
    <>
      <div
        className="drawer-overlay"
        onClick={closeDrawer}
        data-testid="drawer-overlay"
        aria-hidden="true"
      />
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={t.drawer.title}
        data-testid="drawer"
      >
        <header className="drawer__head">
          <div className="drawer__head-icon" aria-hidden="true">
            <IconMail className="icon" />
          </div>
          <div className="drawer__head-body">
            <div className="drawer__head-title">{t.drawer.title}</div>
            <div className="drawer__head-sub mono">
              {selectedMessage.file_name || selectedMessage.subject || '—'}
            </div>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            className="btn ghost"
            onClick={closeDrawer}
            aria-label={t.common.close}
            data-testid="drawer-close"
          >
            ×
          </button>
        </header>

        <DrawerTabs active={activeTab} onChange={setTab} statusMap={statusMap} />

        <div className="drawer__body">
          {activeTab === 'gmail' ? <GmailTab message={selectedMessage} /> : null}
          {activeTab === 'ai' ? <AiTab message={selectedMessage} /> : null}
          {activeTab === 'drive' ? <DriveTab message={selectedMessage} /> : null}
          {activeTab === 'bezala' ? (
            <BezalaTab
              message={selectedMessage}
              onRefetch={onRefetch}
              onClose={closeDrawer}
            />
          ) : null}
        </div>
      </aside>
    </>
  );
}
