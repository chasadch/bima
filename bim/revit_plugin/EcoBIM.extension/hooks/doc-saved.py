# -*- coding: utf-8 -*-
"""
EcoBIM pyRevit Extension Hook — Auto Sync on Save
Triggered automatically every time a Revit document is saved.
"""
import sys
import os

# Add the extension 'lib' directory to sys.path so we can import ecobim_helpers and ecobim_config
lib_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "lib")
if lib_dir not in sys.path:
    sys.path.append(lib_dir)

from pyrevit import script
import ecobim_helpers as helpers

# Get the document being saved
try:
    doc = __eventargs__.Document
except NameError:
    # Fallback if run manually
    from pyrevit import HOST_APP
    doc = HOST_APP.doc

if doc:
    try:
        # Extract values
        wall_u, _, _ = helpers.extract_wall_u(doc)
        roof_u, _, _ = helpers.extract_roof_u(doc)
        win_u, _, _ = helpers.extract_window_u(doc)
        gfa = helpers.extract_gfa(doc)
        quantities = helpers.extract_quantities(doc)

        u_values = {}
        if wall_u is not None:
            u_values["wall_u"] = round(wall_u, 4)
        if roof_u is not None:
            u_values["roof_u"] = round(roof_u, 4)
        if win_u is not None:
            u_values["window_u"] = round(win_u, 4)

        # Push to Flask server
        # Fail silently if backend is not running to avoid interrupting user's work
        helpers.push_to_backend(quantities=quantities, u_values=u_values, gfa=gfa)
    except Exception:
        # Silently ignore errors during auto-saves so the user is never interrupted
        pass
