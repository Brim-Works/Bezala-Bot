import { useEffect, useRef } from 'react';
import {
  IconMail,
  IconSparkle,
  IconDrive,
  IconBezala,
} from '../icons/index.jsx';
import { useI18n } from '../i18n/useI18n.jsx';
import { DRAWER_TABS } from './DrawerProvider.jsx';

const TAB_ICONS = {
  gmail: IconMail,
  ai: IconSparkle,
  drive: IconDrive,
  bezala: IconBezala,
};

/* Flik-rad med piltangents-navigation.
 * Tab-tangenten navigerar ut ur fliklistan enligt normal tab-order.
 * ArrowLeft/ArrowRight byter aktiv flik. */
export default function DrawerTabs({ active, onChange, statusMap }) {
  const { t } = useI18n();
  const listRef = useRef(null);

  useEffect(() => {
    const node = listRef.current;
    if (!node) return undefined;

    function onKey(e) {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      e.preventDefault();
      const idx = DRAWER_TABS.indexOf(active);
      const delta = e.key === 'ArrowRight' ? 1 : -1;
      const next = DRAWER_TABS[(idx + delta + DRAWER_TABS.length) % DRAWER_TABS.length];
      onChange(next);
    }

    node.addEventListener('keydown', onKey);
    return () => node.removeEventListener('keydown', onKey);
  }, [active, onChange]);

  return (
    <div
      ref={listRef}
      className="drawer-tabs"
      role="tablist"
      aria-label={t.drawer.tabsLabel}
    >
      {DRAWER_TABS.map((tab) => {
        const Icon = TAB_ICONS[tab];
        const isActive = tab === active;
        const status = statusMap?.[tab] || 'ok';
        return (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            className={`drawer-tabs__tab drawer-tabs__tab--${status} ${
              isActive ? 'is-active' : ''
            }`}
            onClick={() => onChange(tab)}
            data-testid={`drawer-tab-${tab}`}
          >
            <Icon className="icon sm" />
            <span>{t.drawer.tabs[tab]}</span>
          </button>
        );
      })}
    </div>
  );
}
