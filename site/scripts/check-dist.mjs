// Contrôle de sécurité du site compilé (dist/). Fait échouer le build si :
//  - une page n'a pas la CSP attendue ;
//  - un script, une feuille de style, une image, une vidéo ou un cadre vient
//    d'un autre domaine ;
//  - un script ou un style en ligne exécutable apparaît (bloqué par la CSP,
//    donc cassé en production, ou pire : autorisé un jour par erreur) ;
//  - un attribut on* (onclick…) apparaît ;
//  - un lien interne pointe vers une page ou un fichier absent.
// Sans dépendance : des expressions régulières sur du HTML produit par Astro,
// pas sur du HTML arbitraire.
import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const DIST = fileURLToPath(new URL('../dist/', import.meta.url));
const BASE = '/OBS-Dynamics/';
const SITE = 'https://blackoune.github.io';
const CSP_ATTENDUE = readFileSync(new URL('../src/site.ts', import.meta.url), 'utf8')
  .match(/export const CSP = \[([\s\S]*?)\]\.join/)[1]
  .match(/(["'])(.*?)\1/g)
  .map((s) => s.slice(1, -1))
  .join('; ');

function* pages(dossier) {
  for (const nom of readdirSync(dossier)) {
    const chemin = join(dossier, nom);
    if (statSync(chemin).isDirectory()) yield* pages(chemin);
    else if (nom.endsWith('.html')) yield chemin;
  }
}

function existe(href) {
  const chemin = decodeURI(href.split(/[?#]/)[0]).slice(BASE.length);
  const cible = join(DIST, chemin);
  return existsSync(cible) && (statSync(cible).isFile() || existsSync(join(cible, 'index.html')));
}

const erreurs = [];
let nombre = 0;
for (const fichier of pages(DIST)) {
  nombre++;
  const html = readFileSync(fichier, 'utf8');
  const nom = relative(DIST, fichier);
  const signaler = (msg) => erreurs.push(`${nom} : ${msg}`);

  const csp = html.match(/<meta http-equiv="Content-Security-Policy" content="([^"]*)"/);
  if (!csp) signaler('CSP absente');
  else if (csp[1].replaceAll('&#39;', "'") !== CSP_ATTENDUE) signaler('CSP différente de src/site.ts');

  for (const [, balise, attrs] of html.matchAll(/<(script|link|img|video|audio|source|iframe|embed|object)\b([^>]*)>/gi)) {
    for (const [, , valeur] of attrs.matchAll(/\b(src|href|srcset|poster|data)="([^"]*)"/gi)) {
      const externe = /^(https?:)?\/\//i.test(valeur) && !valeur.startsWith(SITE + BASE);
      // <link rel="canonical|alternate"> désigne une adresse, il ne charge rien.
      const simpleAdresse = /^link$/i.test(balise) && /rel="(canonical|alternate)"/.test(attrs);
      if (externe && !simpleAdresse) signaler(`${balise} chargé depuis un autre domaine : ${valeur}`);
    }
  }

  for (const [, attrs, contenu] of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    const donnees = /type="application\/ld\+json"/i.test(attrs);
    if (!donnees && !/\bsrc="/i.test(attrs)) signaler('script en ligne');
    if (!donnees && contenu.trim() && /\bsrc="/i.test(attrs)) signaler('script externe avec contenu en ligne');
  }
  if (/<style\b/i.test(html)) signaler('feuille de style en ligne (<style>)');
  if (/<[a-z][^>]*\sstyle="/i.test(html)) signaler('attribut style="" (bloqué par la CSP)');
  if (/<[a-z][^>]*\son[a-z]+="/i.test(html)) signaler('attribut on* (gestionnaire en ligne)');
  if (/href="javascript:/i.test(html)) signaler('lien javascript:');

  for (const [, valeur] of html.matchAll(/\b(?:href|src)="(\/[^"]*)"/g)) {
    if (!valeur.startsWith(BASE)) signaler(`lien interne hors de ${BASE} : ${valeur}`);
    else if (!existe(valeur)) signaler(`lien interne cassé : ${valeur}`);
  }
}

if (nombre === 0) erreurs.push('aucune page trouvée dans dist/');
if (erreurs.length) {
  console.error(`check-dist : ${erreurs.length} problème(s)`);
  for (const e of erreurs) console.error('  - ' + e);
  process.exit(1);
}
console.log(`check-dist : ${nombre} page(s) conformes.`);
