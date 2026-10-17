"""Étoiles du dépôt et téléchargements de chaque release (API GitHub publique)."""
import json
import urllib.request

DEPOT = "Blackoune/OBS-Dynamics"


def lire(chemin):
    with urllib.request.urlopen(f"https://api.github.com/repos/{DEPOT}{chemin}", timeout=15) as r:
        return json.load(r)


def main():
    print(f"Etoiles : {lire('')['stargazers_count']}")
    for release in lire("/releases"):
        total = sum(a["download_count"] for a in release["assets"] if a["name"].endswith(".exe"))
        detail = ", ".join(f"{a['name']} {a['download_count']}" for a in release["assets"])
        print(f"{release['tag_name']} : {total} ({detail or 'aucun fichier'})")


if __name__ == "__main__":
    main()
