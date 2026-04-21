/* Översätter backend-status till SPEC:ens två UI-dimensioner.
 *
 * Backend har redan två kolumner:
 *   - ProcessedMessage.status           (t.ex. "saved", "error", "skipped:no_pdf",
 *                                        "skipped:excluded_subject")
 *   - ProcessedMessage.bezala_upload_status  ("success", "pending", "failed",
 *                                             "skipped")
 *
 * SPEC-modellen:
 *   file_status   ∈ {"saved", "error", "skipped"}
 *   bezala_status ∈ {"transferred", "pending", "error", "na"}
 *
 * Regler:
 *   - Alla "skipped:*" grupperas till "skipped".
 *   - Om file_status inte är "saved" → bezala_status = "na" (oavsett vad
 *     backend säger — ingen meningsfull Bezala-state för filer som aldrig
 *     nådde Drive).
 *   - Bezala "success" → "transferred" (SPEC:ens namn)
 *   - Bezala "failed" → "error"
 *   - Bezala "skipped" eller null (saknas) → na
 *   - Bezala "pending" → pending
 */

export function deriveStatuses(message) {
  const rawFile = typeof message?.status === 'string' ? message.status : '';
  const fileRoot = rawFile.split(':')[0];

  let file;
  if (fileRoot === 'saved') file = 'saved';
  else if (fileRoot === 'error') file = 'error';
  else if (fileRoot === 'skipped') file = 'skipped';
  else file = 'error';

  if (file !== 'saved') {
    return { file, bezala: 'na' };
  }

  switch (message?.bezala_upload_status) {
    case 'success':
      return { file, bezala: 'transferred' };
    case 'pending':
      return { file, bezala: 'pending' };
    case 'failed':
      return { file, bezala: 'error' };
    case 'skipped':
      return { file, bezala: 'na' };
    default:
      return { file, bezala: 'pending' };
  }
}

/* Praktisk helper — returnerar message berikat med file_status/bezala_status. */
export function withStatuses(message) {
  const { file, bezala } = deriveStatuses(message);
  return { ...message, file_status: file, bezala_status: bezala };
}
