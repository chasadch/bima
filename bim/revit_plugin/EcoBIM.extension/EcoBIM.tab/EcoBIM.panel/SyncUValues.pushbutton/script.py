# -*- coding: utf-8 -*-
"""
Sync U-values (Wall, Roof, Window) and Gross Floor Area (GFA) from the active
Revit model to the EcoBIM backend.
"""
from pyrevit import HOST_APP, script

import ecobim_helpers as helpers

output = script.get_output()

# Get the active Revit document
doc = HOST_APP.doc
if not doc:
    print("No active Revit document found. Open a document and try again.")
    script.exit()

print("EcoBIM: Extracting U-values & GFA from active document...")

try:
    # Wall U-value
    wall_u, wall_details, wall_warns = helpers.extract_wall_u(doc)
    print("\n--- WALL U-VALUE ---")
    print(wall_details)
    for w in wall_warns:
        print(w)

    # Roof U-value
    roof_u, roof_details, roof_warns = helpers.extract_roof_u(doc)
    print("\n--- ROOF U-VALUE ---")
    print(roof_details)
    for w in roof_warns:
        print(w)

    # Window U-value
    win_u, win_details, win_warns = helpers.extract_window_u(doc)
    print("\n--- WINDOW U-VALUE ---")
    print(win_details)
    for w in win_warns:
        print(w)

    # Gross Floor Area
    gfa = helpers.extract_gfa(doc)
    print("\n--- GROSS FLOOR AREA ---")
    if gfa is not None:
        print("GFA: {:.2f} sq ft".format(gfa))
    else:
        print("No floors found to determine GFA.")

    # Prepare payload
    u_values = {}
    if wall_u is not None:
        u_values["wall_u"] = round(wall_u, 4)
    if roof_u is not None:
        u_values["roof_u"] = round(roof_u, 4)
    if win_u is not None:
        u_values["window_u"] = round(win_u, 4)

    # Push
    success, msg = helpers.push_to_backend(u_values=u_values, gfa=gfa)
    print("\n--- BACKEND SYNC ---")
    if success:
        print("✅ Success! " + msg)
    else:
        print("❌ Sync Failed: " + msg)

except Exception as e:
    import traceback
    print("❌ Extraction/Sync error:")
    print(traceback.format_exc())
