import { defineConfig } from 'astro/config';

export default defineConfig({
  site: 'https://blackoune.github.io',
  base: '/OBS-Dynamics',
  trailingSlash: 'always',
  // La compression retirait l'espace entre un mot et un <strong> passé à la ligne.
  compressHTML: false,
  i18n: {
    defaultLocale: 'fr',
    locales: ['fr', 'en'],
    routing: { prefixDefaultLocale: false },
  },
  build: {
    // CSP `style-src 'self'` : aucune feuille de style en ligne.
    inlineStylesheets: 'never',
  },
  vite: {
    build: {
      // CSP `script-src 'self'` : Vite ne doit pas coller un petit script
      // (ou une police, une image) directement dans la page.
      assetsInlineLimit: 0,
    },
  },
});
