// Sélecteur Windows | Mac. Sans JavaScript, les deux panneaux restent
// affichés l'un sous l'autre ; ce script ne fait que masquer l'autre.
type Systeme = 'windows' | 'mac';

const selecteur = document.querySelector<HTMLElement>('[data-os-selecteur]');
if (selecteur) {
  const onglets = [...selecteur.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
  const panneaux = [...document.querySelectorAll<HTMLElement>('[data-os-panneau]')];

  const choisir = (os: Systeme, focus = false) => {
    for (const onglet of onglets) {
      const actif = onglet.dataset.os === os;
      onglet.setAttribute('aria-selected', String(actif));
      onglet.tabIndex = actif ? 0 : -1;
      if (actif && focus) onglet.focus();
    }
    for (const panneau of panneaux) panneau.hidden = panneau.dataset.osPanneau !== os;
  };

  const plateforme =
    (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ??
    navigator.userAgent;
  choisir(/mac/i.test(plateforme) ? 'mac' : 'windows');
  selecteur.hidden = false;

  for (const onglet of onglets) {
    onglet.addEventListener('click', () => choisir(onglet.dataset.os as Systeme));
    onglet.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      const suivant = onglets[(onglets.indexOf(onglet) + 1) % onglets.length];
      choisir(suivant.dataset.os as Systeme, true);
    });
  }
}
