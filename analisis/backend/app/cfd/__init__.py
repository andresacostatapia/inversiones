"""Copiloto de Trading CFD (XTB xStation): módulo aparte de la cartera de largo plazo.

Analiza 6 CFD (GOLD, SILVER, US500, US100, US30, US2000) en temporalidades de 1 minuto a 1 día, arma un puntaje
de −100 a +100 y prepara un "ticket" (entrada, stop, objetivo y lotes) con precios traducidos a los de xStation.
Nunca envía órdenes: la ejecución es siempre manual en xStation. Herramienta educativa, no es asesoría financiera.
"""
