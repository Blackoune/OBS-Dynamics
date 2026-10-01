// Contrôle des données de la FAQ (src/data/faq.<langue>.json).
// Lancer : npm test
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, existsSync } from 'node:fs';

const DATA = new URL('../src/data/', import.meta.url);
const langues = readdirSync(DATA).filter((f) => /^faq\.\w+\.json$/.test(f));
const lire = (f) => JSON.parse(readFileSync(new URL(f, DATA), 'utf8'));
const ids = (faq) => faq.categories.map((c) => [c.id, c.symptoms.map((s) => s.id)]);

test('le fichier français existe', () => assert.ok(langues.includes('faq.fr.json')));

for (const fichier of langues) {
  const faq = lire(fichier);

  test(`${fichier} : identifiants uniques et bien formés`, () => {
    const vus = new Set();
    for (const c of faq.categories) {
      for (const id of [c.id, ...c.symptoms.map((s) => `${c.id}/${s.id}`)]) {
        assert.match(id, /^[a-z0-9-]+(\/[a-z0-9-]+)?$/, id);
        assert.ok(!vus.has(id), `doublon : ${id}`);
        vus.add(id);
      }
    }
  });

  test(`${fichier} : chaque symptôme a au moins une étape`, () => {
    for (const c of faq.categories)
      for (const s of c.symptoms) assert.ok(s.steps.length > 0, `${c.id}/${s.id}`);
  });

  test(`${fichier} : chaque média référencé existe`, () => {
    for (const c of faq.categories)
      for (const s of c.symptoms)
        if (s.media) assert.ok(existsSync(new URL(`../public/${s.media.src}`, import.meta.url)), s.media.src);
  });

  test(`${fichier} : mêmes identifiants que faq.fr.json`, () => {
    assert.deepEqual(ids(faq), ids(lire('faq.fr.json')));
  });
}
