import { api } from '../api/client.js';

/* Miniatyr av kvittot (bild) eller en PDF-ruta. Klick öppnar filen. */
export default function ReceiptThumb({ receipt, size = 'md' }) {
  if (!receipt || !receipt.message_id || !receipt.drive_file_id) {
    return <div className={`m-thumb m-thumb--${size} m-thumb--empty`}>Ingen fil</div>;
  }
  const url = api.receiptFileUrl(receipt.message_id);
  const isPdf = (receipt.file_name || '').toLowerCase().endsWith('.pdf');
  if (isPdf && size === 'lg') {
    return <iframe className="m-thumb m-thumb--lg" src={url} title="Kvitto" />;
  }
  return (
    <a className={`m-thumb m-thumb--${size}`} href={url} target="_blank" rel="noreferrer">
      {isPdf ? <span className="m-thumb__pdf">PDF</span> : <img src={url} alt="Kvitto" loading="lazy" />}
    </a>
  );
}
