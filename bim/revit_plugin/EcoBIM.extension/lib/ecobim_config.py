# -*- coding: utf-8 -*-
"""
EcoBIM pyRevit Extension — Shared Configuration
Stores the Flask server URL and unit conversion constants.
"""

# Flask backend URL (change this if your server runs on a different host/port)
SERVER_URL = "https://ecobim-bim-2026.azurewebsites.net"

# API endpoints
ENDPOINT_REVIT_PUSH = SERVER_URL + "/api/revit-push"
ENDPOINT_REVIT_PULL = SERVER_URL + "/api/revit-pull"
ENDPOINT_PREDICT = SERVER_URL + "/api/predict"

# Unit conversion constants
# 1 foot = 0.3048 meters
FT_TO_M = 0.3048

# 1 W/(m²·K) = 0.176110 BTU/(h·ft²·°F)
SI_TO_IP_U_VALUE = 0.176110

# 1 sq ft = 0.092903 sq m
SQFT_TO_SQM = 0.092903
