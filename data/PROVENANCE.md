# Provenance des données

- **Jeu** : Bike Sharing Dataset (UCI Machine Learning Repository), fichier `hour.csv` (17 colonnes, relevés horaires 2011–2012).
- **URL** : https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip
- **Licence** : CC BY 4.0. **DOI** : 10.24432/C5W894.
- **Citation** : Fanaee-T, H. (2013). *Bike Sharing* [Dataset]. UCI Machine Learning Repository.
- **Récupération** : `velos fetch` (3 tentatives, rejeu uniquement pour les erreurs réseau/429/5xx). Au premier appel, l'empreinte SHA-256 est écrite dans `data/manifest.json` (À VERSIONNER) ; ensuite tout fichier d'empreinte différente est refusé.
- **Jamais versionnés** : `data/raw`, `data/store`, `data/incoming`, `data/rejected`, `data/state`.
- **Rejeu historique** (exigé par le sujet) : le jeu est historique, il est rejoué par lots mensuels.
  base = 2011 ; lots = 2012-01 … 2012-11 ; corpus de qualification fixe = 2012-12.
  Le corpus n'est jamais utilisé pour entraîner : il sert à comparer les versions entre elles sur le même examen.
- **Données synthétiques** : `velos.synthetic` (tests/CI/modèle de démonstration uniquement). Tout modèle issu de ces données reçoit le verdict `demo-only` et ne peut jamais devenir champion.
