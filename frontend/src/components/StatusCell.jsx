import FileBadge from './FileBadge.jsx';
import BezalaBadge from './BezalaBadge.jsx';

/* Två staplade badges. När bezala_status är 'na' visas bara fil-badgen —
 * INGEN placeholder (per SPEC + README "Status-modell"). */
export default function StatusCell({ fileStatus, bezalaStatus }) {
  return (
    <div className="status-cell">
      <FileBadge status={fileStatus} />
      <BezalaBadge status={bezalaStatus} />
    </div>
  );
}
