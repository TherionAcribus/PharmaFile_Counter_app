# Guide de Compilation pour PharmaFile

Ce document explique comment compiler l'application PySide6 en un exécutable Windows (.exe) fonctionnel.

## 1. Prérequis

Assurez-vous que votre environnement virtuel est activé et que les dépendances sont installées.

### Attention aux versions spécifiques
Pour éviter les erreurs connues lors de la compilation :
- **setuptools** doit être inférieur à la version 70.0.0 (ex: 69.5.1).
  ```bash
  pip install "setuptools<70.0.0"
  ```
- **line_profiler** ne doit pas être utilisé dans le code de production. 
  - Si vous utilisez le décorateur `@profile`, assurez-vous de le remplacer par un décorateur "vide" dans `main.py` avant de compiler, ou de supprimer l'import `line_profiler`.

## 2. Le fichier de configuration (.spec)

Le fichier `PharmaFile.spec` contient toute la configuration pour PyInstaller.
Il gère déjà l'inclusion des dossiers nécessaires :
- `assets/` (images, sons)
- `skins/` (thèmes)
- `templates/` (fichiers HTML)

Si vous ajoutez de nouveaux dossiers de ressources, pensez à les ajouter dans la section `datas=[]` du fichier `.spec`.

Exemple de configuration importante dans `exe = EXE(...)` :
- `console=False` : Pour ne pas avoir de fenêtre noire (invite de commande) au lancement.
- `icon='app.ico'` : Pour définir l'icône de l'application.

## 3. Lancer la compilation

Ouvrez un terminal à la racine du projet (avec votre environnement virtuel activé) et lancez :

```bash
pyinstaller --noconfirm --clean PharmaFile.spec
```

- `--noconfirm` : Écrase le dossier de sortie sans demander confirmation.
- `--clean` : Nettoie les caches de PyInstaller avant de commencer (recommandé).

## 4. Résultat

Une fois terminé, votre application compilée se trouve dans le dossier `dist/PharmaFile/` (ou le nom défini dans le `.spec`).

L'exécutable est : `dist/PharmaFile/PharmaFile.exe`.

## 5. Résolution de problèmes courants

### Erreur "Permission denied"
Si la compilation échoue avec une erreur d'accès au fichier `.exe` :
1. Vérifiez que l'application n'est pas déjà lancée.
2. Fermez toutes les fenêtres de l'application.
3. Si l'erreur persiste, changez temporairement le nom de sortie dans `PharmaFile.spec` (ex: `name='PharmaFile_v2'`).

### Erreur "No module named 'pkg_resources.extern'"
C'est un conflit entre PyInstaller et les versions récentes de setuptools.
-> Réinstallez setuptools en version 69.x : `pip install "setuptools<70.0.0"`

### Erreur "No module named 'line_profiler'"
Vous avez laissé un import de débogage dans `main.py`.
-> Supprimez `from line_profiler import profile` et remplacez-le par :
```python
# Fonction dummy pour éviter l'erreur si @profile est resté dans le code
def profile(func):
    return func
```

