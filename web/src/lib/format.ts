export function formatTime(seconds: number | null | undefined) {
  if (seconds == null) return '';
  const rounded = Math.floor(seconds);
  const h = Math.floor(rounded / 3600);
  const m = Math.floor((rounded % 3600) / 60);
  const s = rounded % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`;
}

export function formatDate(timestamp: number | null | undefined) {
  if (!timestamp) return '';
  return new Date(timestamp * 1000).toLocaleString();
}
