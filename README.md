# Meshpass

Version **0.0.1**

Passerelle autonome entre un réseau **Meshtastic** et un réseau **MeshCore**. Elle relie deux modules radio (USB ou Bluetooth, au choix) et s’administre depuis une page web sur le réseau local. Le Wi-Fi ne sert qu’à cette page : aucun MQTT, aucun pont Internet.

Le dépôt contient aujourd’hui un **banc d’essai**. Les radios sont simulées, le filtrage et les profils de configuration sont réels. Les liaisons Meshtastic (protobuf) et MeshCore (CLI) ne sont pas encore branchées sur du matériel.

Le détail des règles validées est dans le [cahier des charges](cahier-des-charges-passerelle-autonome-meshtastic-⇄-meshcore-xiao-nrf52840-sur-pi-zero-2-w.md).

## Matériel visé

| Rôle | Référence |
| --- | --- |
| Passerelle | Raspberry Pi Zero 2 W (512 Mo). Un Orange Pi Zero 512 Mo convient en USB. |
| Module Meshtastic | Seeed XIAO nRF52840 + WIO-SX1262 |
| Module MeshCore | Même carte, firmware companion |
| Liaisons | USB de préférence. Bluetooth en secours, une interface active par module. |

Le Pi Zero 2 W n’a qu’un port USB données : deux modules USB demandent un hub. Son Wi-Fi et son Bluetooth partagent la même antenne. L’Orange Pi Zero n’a en général pas de Bluetooth : les deux modules restent en USB, et la page web passe par Ethernet.

## Ce que fait le banc d’essai

- Page web en français : tableau de bord, injection de messages, règles, liste noire, connexions, journal, réglages.
- Filtrage en une passe : message direct, écho de nos modules, préfixes `[MT]` / `[MC]`, liste noire, filtres de règle, heures calmes, positions et mesures, doublons, destination déjà servie, quotas.
- Deux quotas par règle (discussion, puis positions et mesures) plus un plafond global.
- Profils complets `normal.conf`, `crise.conf`, etc. : modules, canaux, règles, liste noire, préfixes, quotas. Le mot de passe d’administration n’est pas dans ces fichiers.
- Mot de passe initial : `meshpass`.

## Prérequis

- Python 3.11 ou plus récent.
- `pyserial` (installé par les scripts).
- Linux : `python3-venv`, et `bluez` sur Raspberry Pi.
- Windows : PowerShell. Si Python manque, le script tente `winget`.

## Installation

Les scripts installent les paquets, copient le projet, créent un environnement Python, puis posent un lanceur.

### Raspberry Pi

Depuis la racine du dépôt, sur Raspberry Pi OS :

```bash
bash install/install-raspberry-pi.sh
```

Le service `meshpass` écoute sur le port **8080**. Le lanceur **Passerelle Mesh** est ajouté au bureau et au menu. La commande `meshpass` accepte `start`, `stop`, `restart`, `status` et `open`.

Une nouvelle session est nécessaire pour que le groupe `dialout` donne accès aux ports USB.

### Orange Pi

Sur Armbian ou Debian :

```bash
bash install/install-orange-pi.sh
```

Même service, même port **8080**, mêmes lanceurs. Aucun paquet Bluetooth n’est installé.

### Windows

```bat
install\install-windows.bat
```

Le projet reste dans `C:\Apps\meshpass`. Le lanceur **Passerelle Mesh** est créé sur le bureau et dans le menu Démarrer. La page est sur le port **8091**, pour laisser le 8080 libre.

### Port différent

```bash
MESH_PORT=8090 bash install/install-raspberry-pi.sh
```

```powershell
$env:MESH_PORT = "8090"
.\install\install-windows.ps1
```

## Lancement manuel

```bash
python run.py
```

| Variable | Défaut | Effet |
| --- | --- | --- |
| `MESH_PORT` | `8091` | Port de la page web |
| `MESH_OPEN_BROWSER` | `1` | Ouvre le navigateur. Le service Linux le met à `0`. |

Sous Linux, après l’installation, préférer `meshpass open` : le service utilise le Python de `/opt/meshpass/.venv`.

## Tests

```bash
python -m unittest tests.test_pipeline
```

## Arborescence

```text
run.py              lancement
gateway/            moteur, filtres, serveur web
gateway/web/        page d’administration
tests/              tests du filtrage et des profils
install/            scripts Raspberry Pi, Orange Pi, Windows
data/configs/       profils .conf (image complète, sans mot de passe)
requirements.txt    pyserial
```

`data/config.json` est l’état local, mot de passe compris. Il est ignoré par Git.

## Licence

[MIT](LICENSE). Copyright (c) 2026 Fred F4EED.
