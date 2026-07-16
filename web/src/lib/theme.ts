export type ThemePreference = 'system' | 'light' | 'dark';

const themeStorageKey = 'stt-vault-theme';

export function getThemePreference(): ThemePreference {
  if (typeof localStorage === 'undefined') return 'system';
  const stored = localStorage.getItem(themeStorageKey);
  return stored === 'light' || stored === 'dark' ? stored : 'system';
}

export function setThemePreference(theme: ThemePreference): void {
  if (typeof document === 'undefined' || typeof localStorage === 'undefined') return;
  if (theme === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  localStorage.setItem(themeStorageKey, theme);
}
