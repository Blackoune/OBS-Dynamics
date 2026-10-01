import { url } from '../site';
import { en } from './en';
import { fr } from './fr';

export type Lang = 'fr' | 'en';
export const LANGUES: Lang[] = ['fr', 'en'];
export const TEXTES = { fr, en };

/** Pages du site : même adresse dans les deux langues, préfixe /en/ en anglais. */
export type Page = '' | 'installation' | 'contact' | 'comparatif' | 'mentions-legales' | 'confidentialite';
export const PAGES: Page[] = ['', 'installation', 'contact', 'comparatif', 'mentions-legales', 'confidentialite'];

export function lien(lang: Lang, page: Page = ''): string {
  return url(lang === 'fr' ? page : `en/${page}`);
}
