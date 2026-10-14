# Dosoft

Gestionnaire de fenêtres multi-compte pour Dofus (Python, customtkinter, hooks clavier `keyboard`, pywin32). Windows uniquement.

## Compiler et déployer

```powershell
.\deploy.ps1 -Run        # compile dist\Dosoft.exe, installe dans C:\Program Files\Dosoft, relance l'app
.\deploy.ps1 -SkipBuild  # réinstalle le dernier build sans recompiler
.\deploy.ps1 -Uninstall  # retire l'app et ses raccourcis
```

- Le déploiement demande une invite UAC (copie dans Program Files) et ferme l'instance Dosoft en cours.
- `settings.json` dans le dossier d'installation est conservé d'une version à l'autre.
- Python 3.12 + `.venv` (créé automatiquement par `deploy.ps1` si absent).

## Tester depuis les sources

Sans compiler, avec de vraies fenêtres Dofus ouvertes :

1. Fermer le Dosoft installé (icône de la barre des tâches → Quitter, ou `Stop-Process -Name Dosoft`). Les deux partagent le mutex `DOSOFT_SINGLE_INSTANCE_MUTEX` : sinon une popup propose de tuer l'autre instance.
2. À la racine du dépôt : `.venv\Scripts\python.exe main.py`. Hors admin, le script se relance via UAC avec le dossier du script comme dossier de travail : `skin/`, `sounds/`, `settings.json` et `dosoft.log` sont donc ceux du dépôt (pas ceux de Program Files).
3. Lire `dosoft.log` (racine du dépôt, 1 Mo max + 2 archives `dosoft.log.1/.2`, ignoré par git) : démarrage et version, raccourcis enregistrés/déclenchés, fenêtres trouvées par `scan_slots`, chaque focus, erreurs des hooks, du focus, du moteur de notifications et de `check_version`. Suivi en direct : `Get-Content dosoft.log -Wait -Tail 30`.

- Le log passe par `app_logging.py` (logger `dosoft`). Ne jamais y journaliser une touche qui n'est pas un raccourci reconnu. Dans les boucles de polling, utiliser `log_exception_throttled` pour ne pas inonder le fichier.
- L'exe installé écrit aussi son `dosoft.log` dans son dossier de travail (`C:\Program Files\Dosoft`).

## Cycle de développement

- `main` = version stable, celle installée dans Program Files. Chaque évolution se fait sur une branche dédiée (`feat/...`, `fix/...`), une session Claude Code par évolution.
- Boucle : plan validé par l'utilisateur pour tout changement non trivial → code sur la branche → l'utilisateur teste depuis les sources avec de vraies fenêtres Dofus (Claude ne voit pas le jeu : demander un retour concret) → commits.
- Une fois validé en jeu : incrémenter `version.json`, fusionner dans `main`, proposer `deploy.ps1 -Run`, pousser `main` sur `origin`.
- Versions : 4e chiffre pour les versions perso (1.2.2.1, 1.2.2.2…). `is_newer_version` compare numériquement, donc l'alerte au démarrage ne s'affiche que si l'upstream publie une version supérieure (ex. 1.2.3).
- Remotes : `origin` = paulcarensac-sys/dosoft (fork perso), `upstream` = LuframeCode/dosoft (lecture seule, push désactivé). Intégrer une nouvelle version upstream dans une session dédiée (`git fetch upstream` puis merge sur une branche).

## À savoir avant de modifier le code

- `skin/`, `sounds/`, `logo.ico` et `settings.json` sont lus **relativement au dossier de travail**, pas depuis l'exe : un raccourci doit avoir le dossier d'installation comme dossier de démarrage. `resources/` et `version.json` sont embarqués dans l'exe (`sys._MEIPASS`).
- La version affichée vient de `version.json` (fallback `app_version.py`). Au démarrage, `check_version` compare avec `version.json` du dépôt upstream LuframeCode/Dosoft et affiche une alerte si l'upstream est plus récent (sans bloquer).
- L'app exige les droits administrateur (manifest `uac_admin=True` dans `Dosoft.spec`).
- Ne pas utiliser `build.cmd` (il supprime `Dosoft.spec`, qui est suivi par git) ni `setup.iss` (il ajoute en silence une exclusion Windows Defender).
