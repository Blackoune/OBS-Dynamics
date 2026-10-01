// Dernière release publiée, lue au BUILD uniquement (jamais par le navigateur
// du visiteur : connect-src 'none').
//   - aucune release (404)       -> null : « Première version bientôt disponible »
//   - API ou fichier indisponible -> le build échoue ; le site en ligne reste
//     la version précédente. Jamais d'empreinte absente ou fausse.
import { REPO } from '../site';

export interface Release {
  version: string;
  date: Date;
  taille: number;        // octets
  sha256: string;
  signe: boolean;
  virustotal: string;
  telechargement: string;
}

const API = `https://api.github.com/repos/${REPO}/releases/latest`;

async function lire(url: string, accept: string): Promise<Response> {
  const entetes: Record<string, string> = { Accept: accept, 'User-Agent': 'obs-dynamics-site' };
  if (process.env.GITHUB_TOKEN) entetes.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
  return fetch(url, { headers: entetes });
}

async function charger(): Promise<Release | null> {
  // Aperçu local seulement (npm run apercu) : montre la page avec une release.
  // Impossible en CI (GitHub Actions définit toujours CI=true) : une fausse
  // empreinte n'atteint jamais le site en ligne.
  if (process.env.APERCU_RELEASE === '1' && !process.env.CI) {
    return {
      version: 'v0.0.0-apercu',
      date: new Date('2026-10-01'),
      taille: 77_594_624,
      sha256: '0'.repeat(64),
      signe: false,
      virustotal: 'https://www.virustotal.com/',
      telechargement: '#',
    };
  }
  const reponse = await lire(API, 'application/vnd.github+json');
  if (!reponse.ok) await reponse.body?.cancel();  // un corps non lu garde le socket ouvert
  if (reponse.status === 404) return null;
  if (!reponse.ok) throw new Error(`API GitHub : ${reponse.status} sur ${API}`);
  const r = await reponse.json();

  const exe = r.assets.find((a: { name: string }) => a.name === 'Dynamics.exe');
  const sommes = r.assets.find((a: { name: string }) => a.name === 'SHA256SUMS.txt');
  if (!exe || !sommes) throw new Error(`Release ${r.tag_name} incomplète : Dynamics.exe ou SHA256SUMS.txt absent`);

  const fichier = await lire(sommes.browser_download_url, 'application/octet-stream');
  if (!fichier.ok) throw new Error(`SHA256SUMS.txt : ${fichier.status}`);
  const ligne = (await fichier.text()).split('\n').find((l) => /\sDynamics\.exe$/.test(l.trim()));
  const sha256 = ligne?.trim().split(/\s+/)[0] ?? '';
  if (!/^[0-9a-f]{64}$/.test(sha256)) throw new Error('SHA256SUMS.txt : empreinte de Dynamics.exe illisible');

  return {
    version: r.tag_name,
    date: new Date(r.published_at),
    taille: exe.size,
    sha256,
    signe: !/non signé/i.test(r.body ?? ''),
    virustotal: `https://www.virustotal.com/gui/file/${sha256}`,
    telechargement: `https://github.com/${REPO}/releases/latest/download/Dynamics.exe`,
  };
}

// Mémorisé : les pages qui l'importent partagent un seul appel par build.
let promesse: Promise<Release | null> | undefined;
export function derniereRelease(): Promise<Release | null> {
  return (promesse ??= charger());
}
