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
    </section>
  );
}
