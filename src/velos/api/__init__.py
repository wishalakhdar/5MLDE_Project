"""Service de prédiction (phase 2) : API FastAPI, modèle chargé par alias, traces d'utilisation.

  model_handle.py : charge le modèle désigné par l'alias du registre et ses métadonnées ;
  persistence.py  : enregistre requêtes, prédictions, feedbacks et chargements de modèle ;
  service.py      : règles métier de l'API, indépendantes de FastAPI (testables seules) ;
  schemas.py      : forme des corps JSON acceptés ;
  app.py          : application FastAPI (routes, erreurs, traçage de chaque requête).
"""
