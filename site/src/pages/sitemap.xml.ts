import type { APIRoute } from 'astro';
import { LANGUES, lien, PAGES } from '../i18n';

// Plan du site, avec les versions FR / EN de chaque page liées entre elles.
export const GET: APIRoute = ({ site }) => {
  const abs = (chemin: string) => new URL(chemin, site).href;
  const entrees = PAGES.flatMap((page) => LANGUES.map((lang) => [
    '  <url>',
    `    <loc>${abs(lien(lang, page))}</loc>`,
    ...LANGUES.map((l) => `    <xhtml:link rel="alternate" hreflang="${l}" href="${abs(lien(l, page))}"/>`),
    '  </url>',
  ].join('\n')));
  const xml = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ...entrees,
    '</urlset>',
    '',
  ].join('\n');
  return new Response(xml, { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
};
