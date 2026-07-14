# -*- coding: utf-8 -*-
"""
Sync material quantities from the active Revit model to the EcoBIM backend.
"""
from pyrevit import HOST_APP, script

import ecobim_helpers as helpers

output = script.get_output()

doc = HOST_APP.doc
if not doc:
    print("No active Revit document found. Open a document and try again.")
    script.exit()

print("EcoBIM: Extracting quantities from active document...")

try:
    quantities = helpers.extract_quantities(doc)
    print("\n--- EXTRACTED QUANTITIES ---")
    for k, v in quantities.items():
        print("{}: {}".format(k, v))

    # Push
    success, msg = helpers.push_to_backend(quantities=quantities)
    print("\n--- BACKEND SYNC ---")
    if success:
        print("✅ Success! " + msg)
    else:
        print("❌ Sync Failed: " + msg)

except Exception as e:
    import traceback
    print("❌ Extraction/Sync error:")
    print(traceback.format_exc())
