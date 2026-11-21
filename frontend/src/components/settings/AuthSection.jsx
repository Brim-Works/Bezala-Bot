import { useState } from 'react';
import { api } from '../../api/client.js';
import { useI18n } from '../../i18n/useI18n.jsx';

/* OAuth-anslutningar för Gmail och Drive. Visar varningsbanner när
 * backend-flaggan gmail_auth_required/drive_auth_required är satt
 * (vilket sker automatiskt vid invalid_grant) + en knapp som startar
 * om OAuth-flödet via /api/auth/{service}/start. Knappen navigerar med
 * full sidladdning eftersom Google's OAuth-flöde redirectar tillbaka. */
export default function AuthSection({ form }) {
  const { t } = useI18n();
  const auth = t.settings.auth;

  const gmailNeedsReauth = !!form?.gmail_auth_required;
  const driveNeedsReauth = !!form?.drive_auth_required;

  const priv = form?.gmail_private || {};
  const [privDisconnected, setPrivDisconnected] = useState(false);
  const privConnected = !!priv.connected && !privDisconnected;
  const privNeedsReauth = !!priv.auth_required && privConnected;
  const allowlist = Array.isArray(priv.allowlist) ? priv.allowlist : [];

  const disconnectPrivate = async () => {
    try {
      await api.disconnectPrivateGmail();
      setPrivDisconnected(true);
    } catch (e) {
      /* felet visas av global felhantering */
    }
  };

  const startReauth = (service) => {
    // Full page navigation — vi måste skicka session-cookien till backend
    // och Google's redirect tar oss tillbaka via /api/auth/<service>/callback.
    window.location.href = `/api/auth/${service}/start`;
  };

  return (
    <section className="settings-section" data-testid="auth-section">
      <header className="settings-section__head">
        <h2 className="settings-section__title">{auth.title}</h2>
        <p className="settings-section__lead muted">{auth.lead}</p>
      </header>

      {gmailNeedsReauth && (
        <div className="settings-banner settings-banner--warn" data-testid="gmail-auth-banner">
          <div className="settings-banner__title">{auth.bannerGmailTitle}</div>
          <div className="settings-banner__body muted">{auth.bannerGmail}</div>
        </div>
      )}
      {driveNeedsReauth && (
        <div className="settings-banner settings-banner--warn" data-testid="drive-auth-banner">
          <div className="settings-banner__title">{auth.bannerDriveTitle}</div>
          <div className="settings-banner__body muted">{auth.bannerDrive}</div>
        </div>
      )}

      <div className="settings-auth-row">
        <div className="settings-auth-row__label">
          <span className="settings-field__label">{auth.gmailLabel}</span>
          <span
            className={
              gmailNeedsReauth
                ? 'settings-auth-row__status settings-auth-row__status--err'
                : 'settings-auth-row__status muted'
            }
          >
            {gmailNeedsReauth ? auth.bannerGmailTitle : auth.connected}
          </span>
        </div>
        <button
          type="button"
          className={gmailNeedsReauth ? 'btn primary' : 'btn ghost'}
          onClick={() => startReauth('gmail')}
          data-testid="reconnect-gmail"
        >
          {auth.reconnectGmail}
        </button>
      </div>

      <div className="settings-auth-row">
        <div className="settings-auth-row__label">
          <span className="settings-field__label">{auth.driveLabel}</span>
          <span
            className={
              driveNeedsReauth
                ? 'settings-auth-row__status settings-auth-row__status--err'
                : 'settings-auth-row__status muted'
            }
          >
            {driveNeedsReauth ? auth.bannerDriveTitle : auth.connected}
          </span>
        </div>
        <button
          type="button"
          className={driveNeedsReauth ? 'btn primary' : 'btn ghost'}
          onClick={() => startReauth('drive')}
          data-testid="reconnect-drive"
        >
          {auth.reconnectDrive}
        </button>
      </div>

      <div className="settings-auth-row" data-testid="private-gmail-row">
        <div className="settings-auth-row__label">
          <span className="settings-field__label">{auth.privateGmailLabel}</span>
          <span
            className={
              privNeedsReauth
                ? 'settings-auth-row__status settings-auth-row__status--err'
                : 'settings-auth-row__status muted'
            }
            data-testid="private-gmail-status"
          >
            {privConnected
              ? auth.privateConnectedAs.replace('{email}', priv.email || '—')
              : auth.privateNotConnected}
          </span>
          <span className="muted" data-testid="private-gmail-senders">
            {allowlist.length
              ? `${auth.privateSendersLabel} ${allowlist.join(', ')}`
              : auth.privateNoSenders}
          </span>
          {!privConnected && <span className="muted">{auth.privateHint}</span>}
        </div>
        <div className="settings-auth-row__actions">
          <button
            type="button"
            className={privConnected && !privNeedsReauth ? 'btn ghost' : 'btn primary'}
            onClick={() => startReauth('gmail_private')}
            data-testid="connect-private-gmail"
          >
            {privConnected ? auth.reconnectPrivateGmail : auth.connectPrivateGmail}
          </button>
          {privConnected && (
            <button
              type="button"
              className="btn ghost"
              onClick={disconnectPrivate}
              data-testid="disconnect-private-gmail"
            >
              {auth.disconnectPrivateGmail}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
