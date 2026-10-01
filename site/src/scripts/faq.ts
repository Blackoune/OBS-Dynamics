// Questionnaire de la FAQ. Il ne contient aucun texte : il relit la FAQ
// complète rendue dans la page (source unique : src/data/faq.fr.json).
// Adresse partageable d'une réponse : /contact/#faq/<categorie>/<symptome>.
// La réponse Oui / Non n'est envoyée nulle part.

interface Symptome { id: string; titre: string; etapes: HTMLOListElement }
interface Categorie { id: string; titre: string; symptomes: Symptome[] }

const assistant = document.querySelector<HTMLElement>('[data-faq-assistant]');

if (assistant) {
  const fil = assistant.querySelector<HTMLElement>('[data-faq-fil]')!;
  const zone = assistant.querySelector<HTMLElement>('[data-faq-etape]')!;
  const annonce = assistant.querySelector<HTMLElement>('[data-faq-annonce]')!;
  const modeleDiscord = assistant.querySelector<HTMLTemplateElement>('[data-faq-discord]')!;
  // Textes dans la langue de la page (i18n/*.ts, posés par Contact.astro).
  const T: Record<string, string> = JSON.parse(assistant.dataset.textes ?? '{}');

  const categories: Categorie[] = [...document.querySelectorAll<HTMLElement>('[data-faq-categorie]')].map((s) => ({
    id: s.dataset.faqCategorie!,
    titre: s.dataset.titre!,
    symptomes: [...s.querySelectorAll<HTMLDetailsElement>('[data-faq-symptome]')].map((d) => ({
      id: d.dataset.faqSymptome!,
      titre: d.querySelector('summary')!.textContent!.trim(),
      etapes: d.querySelector('ol')!,
    })),
  }));
  // « Autre question » n'a pas de symptôme : elle mène directement à Discord.
  categories.push({ id: 'autre', titre: T.autre, symptomes: [] });

  const el = <K extends keyof HTMLElementTagNameMap>(balise: K, texte?: string, classe?: string) => {
    const e = document.createElement(balise);
    if (texte) e.textContent = texte;
    if (classe) e.className = classe;
    return e;
  };

  const bouton = (texte: string, action: () => void, classe?: string) => {
    const b = el('button', texte, classe);
    b.type = 'button';
    b.addEventListener('click', action);
    return b;
  };

  const adresse = (chemin: string[]) => {
    history.replaceState(null, '', chemin.length ? `#faq/${chemin.join('/')}` : location.pathname);
  };

  // Chaque nouvelle étape : titre focalisé et annoncé aux lecteurs d'écran.
  // Au chargement de la page (focus = false), rien n'est annoncé ni focalisé.
  const afficher = (titre: string, contenu: Node[], chemin: string[], miettes: string[], focus = true) => {
    const h2 = el('h2', titre);
    h2.tabIndex = -1;
    zone.replaceChildren(h2, ...contenu);
    fil.textContent = miettes.join(' › ');
    if (!focus) return;
    annonce.textContent = titre;
    adresse(chemin);
    h2.focus({ preventScroll: true });
    if (h2.getBoundingClientRect().top < 0) assistant.scrollIntoView();
  };

  const accueil = (focus = true) => {
    const liste = el('ul', undefined, 'choix choix-categories');
    categories.forEach((c, i) => {
      const b = bouton('', () => (c.symptomes.length ? categorie(c) : discord(c)));
      b.append(el('span', String(i + 1).padStart(2, '0'), 'choix-index'), c.titre);
      const li = el('li');
      li.append(b);
      liste.append(li);
    });
    afficher(T.quelProbleme, [liste], [], [], focus);
  };

  const categorie = (c: Categorie) => {
    const liste = el('ul', undefined, 'choix');
    for (const s of c.symptomes) {
      const li = el('li');
      li.append(bouton(s.titre, () => reponse(c, s)));
      liste.append(li);
    }
    afficher(T.quoiExactement, [liste, bouton(T.retour, () => accueil(), 'retour')], [c.id], [c.titre]);
  };

  const reponse = (c: Categorie, s: Symptome) => {
    const etapes = s.etapes.cloneNode(true) as HTMLOListElement;
    etapes.className = 'reponse';
    const question = el('div', undefined, 'satisfait');
    question.append(
      el('p', T.satisfait),
      bouton(T.oui, () => {
        const merci = el('p', T.merci, 'merci');
        merci.setAttribute('role', 'status');
        question.replaceWith(merci);
      }),
      bouton(T.non, () => question.replaceWith(encartDiscord(c, s))),
    );
    afficher(s.titre, [etapes, question, bouton(T.retour, () => categorie(c), 'retour')], [c.id, s.id], [c.titre, s.titre]);
  };

  const discord = (c: Categorie) => {
    afficher(T.discordTitre, [encartDiscord(c), bouton(T.retour, () => accueil(), 'retour')], [c.id], [c.titre]);
  };

  const encartDiscord = (c: Categorie, s?: Symptome) => {
    const encart = modeleDiscord.content.cloneNode(true) as DocumentFragment;
    const resume = s
      ? `${c.titre} > ${s.titre} — ${T.reponseConsultee} : ${location.origin}${location.pathname}#faq/${c.id}/${s.id}`
      : c.titre;
    encart.querySelector('[data-faq-resume]')!.textContent = resume;
    const copier = encart.querySelector<HTMLButtonElement>('[data-faq-copier]')!;
    copier.addEventListener('click', async () => {
      await navigator.clipboard?.writeText(resume);
      copier.textContent = T.copie;
    });
    if (!s) encart.querySelector('h3')?.remove();
    const conteneur = el('div');
    conteneur.append(encart);
    return conteneur;
  };

  // Adresse directe : /contact/#faq/<categorie>/<symptome>
  const ouvrirAdresse = () => {
    const [, idCat, idSym] = location.hash.match(/^#faq\/([\w-]+)(?:\/([\w-]+))?/) ?? [];
    const c = categories.find((x) => x.id === idCat);
    const s = c?.symptomes.find((x) => x.id === idSym);
    if (c && s) reponse(c, s);
    else if (c) (c.symptomes.length ? categorie(c) : discord(c));
    else return false;
    assistant.scrollIntoView();
    return true;
  };

  assistant.hidden = false;
  if (!ouvrirAdresse()) accueil(false);
  window.addEventListener('hashchange', ouvrirAdresse);
}
