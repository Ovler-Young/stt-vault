import { writable } from 'svelte/store';

export const selectedAssetId = writable<string | null>(null);
