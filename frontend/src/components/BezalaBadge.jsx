import Pill from './Pill.jsx';
import { useI18n } from '../i18n/useI18n.jsx';

const KIND = {
  transferred: 'ok',
  pending: 'warn',
  error: 'err',
};

/* Returnerar null för 'na' enligt SPEC — ingen placeholder, ingen badge. */
export default function BezalaBadge({ status }) {
  const { t } = useI18n();
  if (!status || status === 'na') return null;
  const kind = KIND[status];
  if (!kind) return null;
  return <Pill kind={kind}>{t.bezalaStatus[status]}</Pill>;
}
