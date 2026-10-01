// Boutons « Copier » : révélés seulement si le presse-papiers est disponible.
for (const bouton of document.querySelectorAll<HTMLButtonElement>('button[data-copier]')) {
  if (!navigator.clipboard) continue;
  bouton.hidden = false;
  bouton.addEventListener('click', async () => {
    await navigator.clipboard.writeText(bouton.dataset.copier ?? '');
    const avant = bouton.textContent;
    bouton.textContent = bouton.dataset.copie ?? '✓';
    bouton.classList.add('copie');
    setTimeout(() => {
      bouton.textContent = avant;
      bouton.classList.remove('copie');
    }, 1600);
  });
}
