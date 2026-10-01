// Valeurs partagées par toutes les pages.

export const REPO = 'Blackoune/OBS-Dynamics';
export const GITHUB_URL = `https://github.com/${REPO}`;

// Invitations Discord permanentes, créées par Blackoune (plan §13.2 n° 2).
// Tant qu'une valeur est vide, le bouton correspondant n'est pas affiché :
// jamais de lien cassé ni de faux lien en ligne.
export const DISCORD = {
  general: 'https://discord.gg/SND6DmDHrg',
  ticket: '',
  vocal: 'https://discord.gg/MnQw9rkxsX',
};

// Adresse de contact dédiée au projet (plan §13.1 n° 13), en deux morceaux :
// elle n'apparaît jamais entière dans le HTML (robots collecteurs).
// Vide : la carte mail affiche « bientôt disponible ».
export const MAIL = { utilisateur: '', domaine: '' };

// Politique de sécurité du contenu, identique sur chaque page. Vérifiée par
// scripts/check-dist.mjs : une page sans elle fait échouer le build.
export const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "media-src 'self'",
  "connect-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  'upgrade-insecure-requests',
].join('; ');

/** Chemin interne préfixé par `base` : url('installation') -> /OBS-Dynamics/installation/ */
export function url(chemin = ''): string {
  const base = import.meta.env.BASE_URL.replace(/\/$/, '');
  const propre = chemin.replace(/^\//, '');
  const dossier = propre && !propre.endsWith('/') && !propre.includes('.');
  return `${base}/${propre}${dossier ? '/' : ''}`;
}
