// Adresse assemblée ici : le HTML ne contient que « utilisateur [at] domaine ».
const bloc = document.querySelector<HTMLElement>('[data-mail-utilisateur]');
if (bloc) {
  const adresse = `${bloc.dataset.mailUtilisateur}@${bloc.dataset.mailDomaine}`;
  const lien = document.createElement('a');
  lien.href = `mailto:${adresse}`;
  lien.textContent = adresse;
  lien.className = 'mono';
  const copier = document.createElement('button');
  copier.type = 'button';
  copier.className = 'copier';
  copier.dataset.copier = adresse;
  copier.textContent = bloc.dataset.copierLabel ?? '';
  copier.dataset.copie = bloc.dataset.copie;
  bloc.replaceChildren(lien, ' ', copier);
}
