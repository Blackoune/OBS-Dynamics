# Sécurité

## Signaler une faille

Ne publiez pas la faille dans une issue ouverte : elle serait lisible par tout le monde
avant d'être corrigée.

Utilisez le signalement privé de GitHub : onglet **Security** du dépôt, puis
**Report a vulnerability**. Seul le mainteneur voit le message.

Précisez si possible :

- la version d'OBS Dynamics concernée ;
- les étapes pour reproduire le problème ;
- ce qu'un attaquant peut obtenir.

Vous recevez une première réponse sous 7 jours. Une fois la correction publiée, la faille
est décrite dans les notes de version, avec votre nom si vous le souhaitez.

## Versions suivies

Seule la dernière version publiée dans les
[Releases](../../releases/latest) reçoit des correctifs de sécurité.

## Vérifier un `Dynamics.exe` téléchargé

Chaque release contient `Dynamics.exe` et `SHA256SUMS.txt`. Téléchargez-les uniquement
depuis la page Releases de ce dépôt.

1. **Empreinte.** Dans PowerShell, dans le dossier du téléchargement :

   ```powershell
   Get-FileHash .\Dynamics.exe -Algorithm SHA256
   ```

   La valeur affichée doit être identique à celle de `SHA256SUMS.txt`. Si elle diffère,
   supprimez le fichier et ne l'ouvrez pas.

2. **Provenance.** Le `.exe` est construit par GitHub Actions à partir du code du dépôt,
   jamais sur un PC. Avec la [CLI GitHub](https://cli.github.com/) :

   ```powershell
   gh attestation verify .\Dynamics.exe --repo Blackoune/OBS-Dynamics
   ```

   La commande confirme le workflow et le commit qui ont produit le fichier.

3. **Analyse antivirus.** Les notes de chaque release contiennent le lien du rapport
   VirusTotal du fichier publié.
