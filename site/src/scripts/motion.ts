// Animations de la page Présentation. Chacune montre une vraie fonction du
// logiciel ; aucune n'est décorative.
//  - Sans JavaScript, ou avec « réduire les animations » : la page affiche
//    l'état final de chaque démo (écrit dans le HTML), rien ne bouge.
//  - Sous 861 px de large : pas d'épinglage, animations simplifiées.
//  - Une démo ne tourne que lorsqu'elle est visible.
// Les styles passent par le CSSOM (element.style), autorisé par la CSP.
import 'lenis/dist/lenis.css';
import Lenis from 'lenis';
import { gsap } from 'gsap';
import { Flip } from 'gsap/Flip';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { SplitText } from 'gsap/SplitText';

gsap.registerPlugin(Flip, ScrollTrigger, SplitText);

const reduit = matchMedia('(prefers-reduced-motion: reduce)').matches;
const $ = <T extends Element = HTMLElement>(sel: string, racine: ParentNode = document) => racine.querySelector<T>(sel);
const $$ = <T extends Element = HTMLElement>(sel: string, racine: ParentNode = document) => [...racine.querySelectorAll<T>(sel)];

/** Joue `boucle` seulement quand `element` est à l'écran. */
function quandVisible(element: Element | null, boucle: gsap.core.Timeline) {
  if (!element) return;
  boucle.pause();
  new IntersectionObserver(([entree]) => (entree.isIntersecting ? boucle.play() : boucle.pause()), {
    threshold: 0.25,
  }).observe(element);
}

// --------------------------------------------------------------------------
// Widget musique : thèmes de music_style.TEMPLATES, dispositions de LAYOUTS.
// --------------------------------------------------------------------------
interface Theme { nom: string; bg: string; border: string; contour?: boolean; tirets?: boolean; accent?: string; opacite: number; mac?: boolean }
const THEMES: Theme[] = [
  { nom: 'Violet', bg: '#0F0C1B', border: '#A855F7', opacite: 82 },
  { nom: 'Cassette néon', bg: '#150A21', border: '#FF3CAC', opacite: 88 },
  { nom: 'Ardoise', bg: '#1A1530', border: '#2A2145', opacite: 94 },
  { nom: 'Clair', bg: '#F3F0FA', border: '#1A1530', opacite: 92 },
  { nom: 'Sans cadre', bg: '#000000', border: '#000000', contour: false, opacite: 0 },
  { nom: 'Mocha', bg: '#2B1D16', border: '#C98B5E', tirets: true, accent: '#FF8A3D', opacite: 92 },
  { nom: 'macOS sombre', bg: '#1C1C1E', border: '#3A3A3C', accent: '#F2F2F7', opacite: 100, mac: true },
  { nom: 'macOS clair', bg: '#F1F1F3', border: '#D6D6DA', accent: '#1C1C1E', opacite: 100, mac: true },
];
const DISPOSITIONS = [['compact', 'Compact'], ['blocs', 'Blocs'], ['galerie', 'Galerie'], ['minimal', 'Minimal'], ['bandeau', 'Bandeau']];

const rvb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
// Même règle que music_style.is_light : l'encre suit la luminance du fond.
const clair = (hex: string) => {
  const [r, g, b] = rvb(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.179;
};

function appliquerTheme(scene: HTMLElement, t: Theme, disposition: string) {
  const carte = $('.mw-card', scene)!;
  const [r, g, b] = rvb(t.bg);
  const accent = t.accent ?? t.border;
  const sombre = !clair(t.bg) || t.opacite === 0;
  scene.style.setProperty('--fond', `rgba(${r}, ${g}, ${b}, ${t.opacite / 100})`);
  scene.style.setProperty('--contour', t.contour === false ? 'none' : `1px ${t.tirets ? 'dashed' : 'solid'} ${t.border}`);
  scene.style.setProperty('--accent', accent);
  scene.style.setProperty('--marque', accent);
  scene.style.setProperty('--encre', sombre ? '#F3F0FA' : '#1C1C1E');
  scene.style.setProperty('--attenue', sombre ? '#A39BBD' : '#5F5A66');

  const mac = Boolean(t.mac);
  carte.dataset.layout = mac ? 'compact' : disposition;
  carte.classList.toggle('avec-pastilles', mac);
  $('.mw-dots', carte)!.hidden = !mac;
  $('.mw-progress', carte)!.hidden = !mac;
  $('.mw-controls', carte)!.hidden = !mac;
  $('.mw-app', carte)!.hidden = mac;
  $('.mw-frame', carte)!.hidden = !mac;
  $('.mw-discbox', carte)!.hidden = mac || disposition === 'minimal';
  $('.mw-wave', carte)!.hidden = mac || disposition === 'minimal';
  $('[data-mw-theme]', scene)!.textContent = t.nom;
  $('[data-mw-layout]', scene)!.textContent = mac ? 'Compact' : DISPOSITIONS.find(([k]) => k === disposition)![1];
}

function demoWidget() {
  const scene = $('[data-demo="widget"]');
  if (!scene) return;
  const barres = $$('.mw-wave i', scene);
  // Forme d'onde fictive : somme de sinus, sans son réel.
  const onde = (temps: number) =>
    barres.forEach((barre, i) => {
      const v = 0.55 + 0.25 * Math.sin(temps * 3.1 + i * 0.45) + 0.2 * Math.sin(temps * 7.3 + i * 1.7);
      barre.style.transform = `scaleY(${Math.max(0.06, Math.min(1, v)).toFixed(3)})`;
    });
  if (reduit) return onde(1);

  let n = 0;
  const boucle = gsap.timeline({ repeat: -1 });
  boucle.to({}, { duration: 2.6, onUpdate: () => onde(performance.now() / 1000) });
  boucle.call(() => {
    n += 1;
    const theme = THEMES[n % THEMES.length];
    const disposition = DISPOSITIONS[Math.floor(n / 2) % DISPOSITIONS.length][0];
    gsap.fromTo($('.mw-card', scene), { autoAlpha: 0, y: 6 }, { autoAlpha: 1, y: 0, duration: 0.35 });
    appliquerTheme(scene, theme, disposition);
  });
  quandVisible(scene, boucle);
}

// --------------------------------------------------------------------------
// Chat Twitch : messages fictifs, construits comme overlay_server (render).
// --------------------------------------------------------------------------

function demoChat() {
  const wrap = $('[data-chat-wrap]');
  if (!wrap) return;
  // [auteur, couleur, texte], dans la langue de la page (i18n/*.ts).
  const MESSAGES: string[][] = JSON.parse(wrap.dataset.messages ?? '[]');
  let i = 0;
  const ajouter = () => {
    const [auteur, couleur, texte] = MESSAGES[i++ % MESSAGES.length];
    const el = document.createElement('div');
    el.className = 'msg';
    el.style.setProperty('--accent', couleur);
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.style.background = couleur;
    tag.textContent = 'twitch';
    const qui = document.createElement('span');
    qui.className = 'who';
    qui.style.color = couleur;
    qui.textContent = auteur;
    const txt = document.createElement('span');
    txt.textContent = texte;
    el.append(tag, qui, txt);
    wrap.append(el);
    while (wrap.children.length > 5) wrap.firstElementChild!.remove();
  };
  for (let k = 0; k < 4; k++) ajouter();
  if (reduit) return;
  const boucle = gsap.timeline({ repeat: -1 }).to({}, { duration: 1.7 }).call(ajouter);
  quandVisible(wrap, boucle);
}

// --------------------------------------------------------------------------
// Accroche : l'écran passe de « menu » à « en jeu », la scène OBS suit.
// --------------------------------------------------------------------------
function accroche() {
  const titre = $('[data-motion-titre]');
  // Grand écran seulement : sur mobile, redécouper le titre repeint le plus
  // grand élément de la page et retarde son affichage final (LCP).
  if (titre && !reduit && matchMedia('(min-width: 861px)').matches) {
    const mots = SplitText.create(titre, { type: 'words', mask: 'words' });
    gsap.from(mots.words, { yPercent: 110, duration: 0.9, ease: 'expo.out', stagger: 0.06, delay: 0.1 });
  }

  const regie = $('[data-motion="regie"]');
  if (!regie || reduit) return;
  const ecran = $('.ecran', regie)!;
  const scan = $('.ecran-scan', regie)!;
  const statut = $('[data-statut]', regie)!;
  const scenes = $$('[data-scene]', regie);

  const basculer = (etat: 'menu' | 'jeu', texte: string) => () => {
    ecran.dataset.etat = etat;
    scenes.forEach((s) => s.classList.toggle('actif', s.dataset.scene === etat));
    statut.textContent = texte;
  };
  const balayage = (tl: gsap.core.Timeline) =>
    tl.call(() => { statut.textContent = regie.dataset.lecture!; })
      .fromTo(scan, { xPercent: -100, autoAlpha: 1 }, { xPercent: 340, duration: 0.9, ease: 'none' })
      .set(scan, { autoAlpha: 0 });

  const boucle = gsap.timeline({ repeat: -1 });
  boucle.to({}, { duration: 2.2 });
  balayage(boucle).call(basculer('menu', regie.dataset.menu!));
  boucle.to({}, { duration: 2.4 });
  balayage(boucle).call(basculer('jeu', regie.dataset.jeu!));
  quandVisible(regie, boucle);
}

// --------------------------------------------------------------------------
// Comment ça marche : section épinglée sur grand écran, trois temps au scroll.
// --------------------------------------------------------------------------
function mecanisme(mm: gsap.MatchMedia) {
  const section = $('[data-motion="mecanisme"]');
  if (!section) return;
  mm.add('(min-width: 861px) and (prefers-reduced-motion: no-preference)', () => {
    section.classList.add('epingle');
    const panneaux = $$('[data-panneau]', section);
    const temps = $$('[data-temps]', section);
    const etape = (n: number) => temps.forEach((t, i) => t.classList.toggle('actif', i === n));
    etape(0);
    gsap.set(panneaux.slice(1), { autoAlpha: 0, y: 24 });

    gsap.timeline({
      scrollTrigger: {
        trigger: section, start: 'center center', end: '+=140%', pin: true, scrub: 0.6,
        onUpdate: ({ progress }) => etape(progress < 0.33 ? 0 : progress < 0.66 ? 1 : 2),
      },
    })
      .to(panneaux[0], { autoAlpha: 0, y: -24 }, 0.3)
      .to(panneaux[1], { autoAlpha: 1, y: 0 }, 0.4)
      .fromTo($$('.cadre', panneaux[1]), { scale: 0.6, autoAlpha: 0 }, { scale: 1, autoAlpha: 1, stagger: 0.05 }, 0.45)
      .to(panneaux[1], { autoAlpha: 0, y: -24 }, 1.1)
      .to(panneaux[2], { autoAlpha: 1, y: 0 }, 1.2)
      .to({}, { duration: 0.4 });

    return () => {
      section.classList.remove('epingle');
      temps.forEach((t) => t.classList.remove('actif'));
    };
  });
}

// --------------------------------------------------------------------------
// Bibliothèque : la grille passe de 4 à 8 puis 2 colonnes (Flip).
// --------------------------------------------------------------------------
function bibliotheque() {
  const grille = $('[data-motion="biblio"]');
  if (!grille || reduit) return;
  const cycle = ['8', '2', '4'];
  let n = 0;
  const boucle = gsap.timeline({ repeat: -1 }).to({}, { duration: 2.4 }).call(() => {
    const etat = Flip.getState($$('.jaquette', grille));
    grille.dataset.colonnes = cycle[n++ % cycle.length];
    Flip.from(etat, { duration: 0.75, ease: 'power3.inOut', stagger: 0.02 });
  });
  quandVisible(grille, boucle);
}

// --------------------------------------------------------------------------
// Hotkeys : F2 force « Menu », F1 « En jeu », F3 « Inactif ».
// --------------------------------------------------------------------------
function hotkeys() {
  const demo = $('[data-motion="hotkeys"]');
  if (!demo || reduit) return;
  const touches = $$('.touche', demo);
  const etat = $('[data-etat-force]', demo)!;
  const presser = (i: number) => () => {
    touches[i].classList.add('enfoncee');
    etat.textContent = touches[i].querySelector('small')!.textContent;
    gsap.delayedCall(0.18, () => touches[i].classList.remove('enfoncee'));
  };
  const boucle = gsap.timeline({ repeat: -1 });
  for (const i of [1, 0, 2, 0]) boucle.to({}, { duration: 1.3 }).call(presser(i));
  quandVisible(demo, boucle);
}

// --------------------------------------------------------------------------
// Raccourci en mode maintien : la carte est masquée tant que la touche est tenue.
// --------------------------------------------------------------------------
function raccourci() {
  const demo = $('[data-motion="raccourci"]');
  if (!demo) return;
  const touches = $$('.touche', demo);
  const cache = $('.cache', demo)!;
  const tenir = (oui: boolean) => () => {
    touches.forEach((t) => t.classList.toggle('enfoncee', oui));
    cache.classList.toggle('visible', oui);
  };
  if (reduit) return tenir(true)();
  const boucle = gsap.timeline({ repeat: -1 })
    .to({}, { duration: 1.2 }).call(tenir(true))
    .to({}, { duration: 1.8 }).call(tenir(false));
  quandVisible(demo, boucle);
}

// --------------------------------------------------------------------------
// 39 langues : le libellé change, l'axe de largeur d'Archivo s'ouvre.
// --------------------------------------------------------------------------
function langues() {
  const demo = $('[data-motion="langues"]');
  if (!demo || reduit) return;
  const mot = $('[data-mot]', demo)!;
  const items = $$('li', demo);
  let n = 0;
  mot.style.fontVariationSettings = "'wdth' var(--wdth, 100)";
  items[0].classList.add('actif');
  const boucle = gsap.timeline({ repeat: -1 }).to({}, { duration: 1.1 }).call(() => {
    n = (n + 1) % items.length;
    items.forEach((li, i) => li.classList.toggle('actif', i === n));
    mot.textContent = items[n].textContent;
    mot.lang = items[n].lang;
    gsap.fromTo(mot, { autoAlpha: 0, '--wdth': 62 }, { autoAlpha: 1, '--wdth': 100, duration: 0.5, ease: 'power2.out' });
  });
  quandVisible(demo, boucle);
}

// --------------------------------------------------------------------------
if (!reduit) {
  const lenis = new Lenis({ autoRaf: false });
  lenis.on('scroll', ScrollTrigger.update);
  gsap.ticker.add((t) => lenis.raf(t * 1000));
  gsap.ticker.lagSmoothing(0);
}

accroche();
mecanisme(gsap.matchMedia());
bibliotheque();
hotkeys();
raccourci();
demoChat();
demoWidget();
langues();
