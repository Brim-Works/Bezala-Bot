import Pill from './Pill.jsx';
import { useI18n } from '../i18n/useI18n.jsx';

const KIND = {
  saved: 'ok',
  error: 'err',
  skipped: 'muted',
};

export default function FileBadge({ status }) {
  const { t } = useI18n();
  const kind = KIND[status];
  if (!kind) return null;
  return <Pill kind={kind}>{t.fileStatus[status]}</Pill>;
}
