# -*- coding: utf-8 -*-
"""
EcoBIM pyRevit Extension — Revit API Helpers
Provides functions to extract U-values, quantities, and GFA from the active
Revit document, and to POST data to the Flask backend.
"""

import json
import math

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    BuiltInParameter,
    Wall,
    WallType,
    ElementId,
    FamilyInstance,
)

from pyrevit import script

import ecobim_config as cfg

output = script.get_output()

# ------------------------------------------------------------------ #
#  HTTP helper (works in both IronPython 2.7 and CPython 3)
# ------------------------------------------------------------------ #
def _post_json(url, payload_dict):
    """Send a JSON POST request and return the parsed response dict."""
    data = json.dumps(payload_dict)

    # Try .NET WebClient first (always available in pyRevit/IronPython)
    try:
        from System.Net import WebClient
        from System.Text import Encoding
        client = WebClient()
        client.Headers.Add("Content-Type", "application/json")
        raw = client.UploadString(url, "POST", data)
        return json.loads(raw)
    except Exception:
        pass

    # Fallback: Python 3 urllib
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=data.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass

    # Fallback: Python 2 urllib2
    try:
        import urllib2
        req = urllib2.Request(url, data, {"Content-Type": "application/json"})
        resp = urllib2.urlopen(req)
        return json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(
            "Could not reach the Flask server at {}.\n"
            "Make sure it is running (python app.py).\n"
            "Error: {}".format(url, e)
        )


# ------------------------------------------------------------------ #
#  Read U-value directly from type's Analytical Properties
#  (preferred — matches the value Revit displays in Type Properties)
# ------------------------------------------------------------------ #
_U_PARAM_NAMES = [
    "U-Value",
    "U-Factor",
    "Uw",
    "Thermal Transmittance",
    "Thermal Transmittance (U)",
    "Heat Transfer Coefficient (U)",
    "Heat Transfer Coefficient",
]


def _is_heat_transfer_param(param):
    """
    Check if a parameter is defined as a thermal transmittance / heat transfer coefficient parameter.
    If it is, Revit stores its value in internal SI units (W/(m²·K)).
    If it is a simple number / unitless parameter, the user-typed value is returned directly (usually IP units).
    """
    try:
        # Revit 2022+ check
        if hasattr(param.Definition, "GetDataType"):
            dtype = param.Definition.GetDataType()
            dtype_id = dtype.TypeId.lower()
            if "heat" in dtype_id or "transmittance" in dtype_id or "u_value" in dtype_id or "u-value" in dtype_id:
                return True
    except Exception:
        pass

    try:
        # Revit 2021 and earlier check
        if hasattr(param.Definition, "ParameterType"):
            ptype = param.Definition.ParameterType
            if str(ptype) == "HeatTransfer" or "heat" in str(ptype).lower():
                return True
    except Exception:
        pass

    return False


def _get_parameter_by_name_fuzzy(element, candidate_names):
    """
    Look up a parameter on an element by matching candidate names case-insensitively
    and punctuation/space-insensitively.
    Prioritizes the exact order of candidate_names.
    """
    if not element:
        return None

    params_dict = {}
    try:
        for param in element.Parameters:
            name_norm = "".join(c for c in param.Definition.Name.lower() if c.isalnum())
            if name_norm not in params_dict:
                params_dict[name_norm] = param
    except Exception:
        pass

    # 1. Exact normalized matches of candidates
    for candidate in candidate_names:
        cand_norm = "".join(c for c in candidate.lower() if c.isalnum())
        if cand_norm in params_dict:
            return params_dict[cand_norm]

    # 2. Substring normalized matches for any parameter containing candidate patterns
    fallback_keywords = ["uvalue", "ufactor", "uw", "thermaltransmittance", "heattransfercoefficient"]
    for param_norm, param in params_dict.items():
        for kw in fallback_keywords:
            if kw in param_norm:
                return param

    return None


def _get_u_si_from_parameter(param):
    """
    Retrieve U-value from a Revit parameter and ensure it is returned in SI units (W/m²·K).
    Handles native thermal parameters (stored in SI), custom number parameters (stored in IP),
    and text/string parameters.
    """
    if not param or not param.HasValue:
        return None

    val = 0.0
    storage_type = param.StorageType

    # Check StorageType (safe case-insensitive check)
    storage_str = str(storage_type).lower()
    if "double" in storage_str:
        val = param.AsDouble()
    elif "string" in storage_str:
        txt = param.AsString()
        if txt:
            # strip units/whitespace if any, e.g. "0.2563 Btu/(h·ft²·°F)" -> "0.2563"
            txt_clean = "".join(c for c in txt if c.isdigit() or c == ".")
            try:
                val = float(txt_clean)
            except Exception:
                pass
    elif "integer" in storage_str or "int" in storage_str:
        val = float(param.AsInteger())

    if val <= 0:
        return None

    if _is_heat_transfer_param(param):
        # Native parameter: Revit stores this in SI internally
        return val
    else:
        # Custom / Number parameter: assume value is in IP, convert to SI internally
        return val / cfg.SI_TO_IP_U_VALUE


def _get_u_from_type_param(element_type):
    """
    Read the Heat Transfer Coefficient (U) directly from the element type's
    built-in analytical properties or custom parameters.

    Returns U in SI units (W/(m²·K)) or None.
    """
    # 1. Try fuzzy parameter lookup first (prioritize custom overrides)
    try:
        param = _get_parameter_by_name_fuzzy(element_type, _U_PARAM_NAMES)
        val = _get_u_si_from_parameter(param)
        if val is not None:
            return val
    except Exception:
        pass

    # 2. Fall back to built-in parameter ANALYTICAL_HEAT_TRANSFER_COEFFICIENT
    try:
        param = element_type.get_Parameter(
            BuiltInParameter.ANALYTICAL_HEAT_TRANSFER_COEFFICIENT
        )
        val = _get_u_si_from_parameter(param)
        if val is not None:
            return val
    except Exception:
        pass

    return None


def _get_u_value_from_compound(host_type, doc):
    """
    Calculate the U-value (W/m²·K) of a WallType or RoofType from its
    CompoundStructure layers.

    Returns (u_value_si, warnings_list) or (None, warnings_list).
    """
    cs = host_type.GetCompoundStructure()
    if cs is None:
        return None, ["Type '{}' has no compound structure".format(
            host_type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
        )]

    total_r = 0.0
    warnings = []

    for idx in range(cs.LayerCount):
        width_ft = cs.GetLayerWidth(idx)          # feet (Revit internal)
        width_m = width_ft * cfg.FT_TO_M          # metres
        mat_id = cs.GetMaterialId(idx)

        if mat_id == ElementId.InvalidElementId or width_m <= 0:
            continue

        mat = doc.GetElement(mat_id)
        if mat is None:
            continue

        # Get ThermalAsset from material
        thermal_asset_id = mat.ThermalAssetId
        if thermal_asset_id == ElementId.InvalidElementId:
            warnings.append(
                u"  ⚠ Material '{}' has no thermal properties — layer skipped"
                .format(mat.Name)
            )
            continue

        prop_elem = doc.GetElement(thermal_asset_id)
        if prop_elem is None:
            continue

        try:
            thermal_asset = prop_elem.GetThermalAsset()
            conductivity = thermal_asset.ThermalConductivity   # W/(m·K)
        except Exception:
            warnings.append(
                u"  ⚠ Could not read conductivity for '{}'"
                .format(mat.Name)
            )
            continue

        if conductivity > 0:
            total_r += width_m / conductivity   # m²·K / W
        else:
            warnings.append(
                u"  ⚠ Material '{}' conductivity is zero — layer skipped"
                .format(mat.Name)
            )

    if total_r > 0:
        return 1.0 / total_r, warnings      # U in W/(m²·K)
    return None, warnings


# ------------------------------------------------------------------ #
#  Extract Wall U-value (area-weighted average)
# ------------------------------------------------------------------ #
def extract_wall_u(doc):
    """
    Returns (u_value_ip, details_string) for the area-weighted average
    wall U-value in IP units (Btu/h·ft²·°F).
    """
    walls = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Walls)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    sum_ua = 0.0
    sum_area = 0.0
    all_warnings = []
    type_cache = {}  # type_id -> (u_si, warnings)

    for wall in walls:
        type_id = wall.GetTypeId()
        area_param = wall.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
        if area_param is None:
            continue
        area_sqft = area_param.AsDouble()  # sq ft
        if area_sqft <= 0:
            continue

        # Cache U-value per type
        if type_id not in type_cache:
            wall_type = doc.GetElement(type_id)
            if wall_type is None:
                continue
            
            # Filter: Only use walls containing brick materials in their layers AND having function set to Exterior (1)
            type_name = wall_type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString() or ""
            func_param = wall_type.get_Parameter(BuiltInParameter.FUNCTION_PARAM)
            is_exterior = func_param and func_param.HasValue and func_param.AsInteger() == 1
            
            has_brick_layer = False
            cs = wall_type.GetCompoundStructure()
            if cs:
                for idx in range(cs.LayerCount):
                    mat_id = cs.GetMaterialId(idx)
                    if mat_id != ElementId.InvalidElementId:
                        mat = doc.GetElement(mat_id)
                        if mat and any(kw in mat.Name.lower() for kw in _KEYWORDS_BRICK):
                            has_brick_layer = True
                            break
            
            # Fallback: if compound structure is not queryable, check the type name as fallback
            if not has_brick_layer:
                if any(kw in type_name.lower() for kw in _KEYWORDS_BRICK):
                    has_brick_layer = True

            if not has_brick_layer or not is_exterior:
                type_cache[type_id] = (None, [])
                continue
                
            # Try reading U-value directly from the type parameter first (includes air films, matches Revit UI)
            u_si = _get_u_from_type_param(wall_type)
            warns = []
            if u_si is None:
                # Fallback to compound layers calculation
                u_si, warns = _get_u_value_from_compound(wall_type, doc)
                if u_si is None:
                    warns.append(
                        u"  ⚠ Wall type '{}' has no thermal data"
                        .format(type_name)
                    )
            type_cache[type_id] = (u_si, warns)

        u_si, warns = type_cache[type_id]
        all_warnings.extend(warns)

        if u_si is not None:
            sum_ua += u_si * area_sqft
            sum_area += area_sqft

    if sum_area > 0:
        avg_u_si = sum_ua / sum_area
        avg_u_ip = avg_u_si * cfg.SI_TO_IP_U_VALUE
        detail = (
            u"Wall U-value: {:.4f} Btu/(h·ft²·°F)  "
            u"[{:.4f} W/(m²·K)]  —  from {:.0f} ft² of wall area"
            .format(avg_u_ip, avg_u_si, sum_area)
        )
        return avg_u_ip, detail, all_warnings
    return None, "No walls with thermal data found.", all_warnings


# ------------------------------------------------------------------ #
#  Extract Roof U-value (area-weighted average)
# ------------------------------------------------------------------ #
def extract_roof_u(doc):
    """
    Returns (u_value_ip, details_string) for the area-weighted average
    roof U-value in IP units.
    """
    roofs = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    sum_ua = 0.0
    sum_area = 0.0
    all_warnings = []
    type_cache = {}

    for roof in roofs:
        type_id = roof.GetTypeId()
        area_param = roof.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
        if area_param is None:
            continue
        area_sqft = area_param.AsDouble()
        if area_sqft <= 0:
            continue

        if type_id not in type_cache:
            roof_type = doc.GetElement(type_id)
            if roof_type is None:
                continue
            # Try reading U-value directly from the type parameter first (includes air films, matches Revit UI)
            u_si = _get_u_from_type_param(roof_type)
            warns = []
            if u_si is None:
                # Fallback to compound layers calculation
                u_si, warns = _get_u_value_from_compound(roof_type, doc)
                if u_si is None:
                    warns.append(
                        u"  ⚠ Roof type '{}' has no thermal data"
                        .format(
                            roof_type.get_Parameter(
                                BuiltInParameter.ALL_MODEL_TYPE_NAME
                            ).AsString() if roof_type else "Unknown"
                        )
                    )
            type_cache[type_id] = (u_si, warns)

        u_si, warns = type_cache[type_id]
        all_warnings.extend(warns)

        if u_si is not None:
            sum_ua += u_si * area_sqft
            sum_area += area_sqft

    if sum_area > 0:
        avg_u_si = sum_ua / sum_area
        avg_u_ip = avg_u_si * cfg.SI_TO_IP_U_VALUE
        detail = (
            u"Roof U-value: {:.4f} Btu/(h·ft²·°F)  "
            u"[{:.4f} W/(m²·K)]  —  from {:.0f} ft² of roof area"
            .format(avg_u_ip, avg_u_si, sum_area)
        )
        return avg_u_ip, detail, all_warnings
    return None, "No roofs with thermal data found.", all_warnings


# ------------------------------------------------------------------ #
#  Extract Window U-value (area-weighted average)
# ------------------------------------------------------------------ #

# Common parameter names that Revit window families use for thermal transmittance
_WINDOW_THERMAL_PARAM_NAMES = [
    "U-Value",
    "U-Factor",
    "Uw",
    "Thermal Transmittance",
    "Thermal Transmittance (U)",
    "Heat Transfer Coefficient (U)",
    "Heat Transfer Coefficient",
]


def _is_custom_u_value(val):
    """Check if the U-value differs from standard Revit fallback defaults."""
    if val is None:
        return False
    # Standard Revit default fallbacks: Single (3.6886), Double (2.0028), Triple (1.4553)
    for default_val in [3.6886, 2.0028, 1.4553]:
        if abs(val - default_val) <= 0.001:
            return False
    return True


def _get_window_u_from_type(win_type, doc):
    """
    Try to read a thermal transmittance (U-value) from a window FamilySymbol.
    Returns (U in W/(m²·K) or None, is_custom boolean).
    """
    if win_type is None:
        return None, False
        
    try:
        type_name = win_type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
        print("Debugging Window Type '{}':".format(type_name))
    except Exception:
        print("Debugging Window Type (Could not read name)")

    # 1. Try fuzzy parameter lookup first (prioritize custom overrides)
    try:
        param = _get_parameter_by_name_fuzzy(win_type, _WINDOW_THERMAL_PARAM_NAMES)
        if param:
            val = _get_u_si_from_parameter(param)
            print("  - Fuzzy match found: '{}'".format(param.Definition.Name))
            print("    Raw value (AsDouble): {}".format(param.AsDouble()))
            print("    Storage type: {}".format(param.StorageType))
            try:
                print("    GetDataType TypeId: {}".format(param.Definition.GetDataType().TypeId))
            except Exception:
                pass
            try:
                print("    ParameterType: {}".format(param.Definition.ParameterType))
            except Exception:
                pass
            print("    _is_heat_transfer_param: {}".format(_is_heat_transfer_param(param)))
            print("    Computed SI value: {}".format(val))
            if val is not None:
                is_custom = _is_custom_u_value(val)
                return val, is_custom
        else:
            print("  - No fuzzy match found in parameters.")
    except Exception as ex:
        print("  - Error in fuzzy match: {}".format(ex))

    # 2. Fall back to the built-in analytical heat transfer coefficient parameter
    try:
        param = win_type.get_Parameter(
            BuiltInParameter.ANALYTICAL_HEAT_TRANSFER_COEFFICIENT
        )
        if param and param.HasValue:
            val = _get_u_si_from_parameter(param)
            print("  - Built-in parameter ANALYTICAL_HEAT_TRANSFER_COEFFICIENT found.")
            print("    Raw value (AsDouble): {}".format(param.AsDouble()))
            print("    _is_heat_transfer_param: {}".format(_is_heat_transfer_param(param)))
            print("    Computed SI value: {}".format(val))
            if val is not None:
                is_custom = _is_custom_u_value(val)
                return val, is_custom
        else:
            print("  - Built-in parameter not set or empty.")
    except Exception as ex:
        print("  - Error in built-in: {}".format(ex))

    return None, False


def _get_window_area(win, doc):
    """
    Retrieve window area in sq ft.
    Tries HOST_AREA_COMPUTED, common named parameters, type parameters,
    and falls back to Width x Height (checking both instance and type levels).
    """
    area_sqft = 0.0
    
    # 1. Built-in instance computed area
    p = win.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
    if p and p.HasValue and p.AsDouble() > 0:
        return p.AsDouble()

    # 2. Lookup named parameters on instance
    for name in ["Area", "Window Area", "Glazing Area", "Window_Area"]:
        p = win.LookupParameter(name)
        if p and p.HasValue and p.AsDouble() > 0:
            return p.AsDouble()

    # 3. Lookup named parameters on type
    type_id = win.GetTypeId()
    win_type = doc.GetElement(type_id) if type_id != ElementId.InvalidElementId else None
    if win_type:
        for name in ["Area", "Window Area", "Glazing Area", "Window_Area"]:
            p = win_type.LookupParameter(name)
            if p and p.HasValue and p.AsDouble() > 0:
                return p.AsDouble()

    # 4. Fallback: Width x Height (instance first, then type)
    w_val = 0.0
    h_val = 0.0

    # Instance width/height
    w_param = win.get_Parameter(BuiltInParameter.WINDOW_WIDTH) or win.LookupParameter("Width")
    h_param = win.get_Parameter(BuiltInParameter.WINDOW_HEIGHT) or win.LookupParameter("Height")
    if w_param and w_param.HasValue and w_param.AsDouble() > 0:
        w_val = w_param.AsDouble()
    if h_param and h_param.HasValue and h_param.AsDouble() > 0:
        h_val = h_param.AsDouble()

    # Type width/height
    if w_val <= 0 or h_val <= 0:
        if win_type:
            w_param_t = win_type.get_Parameter(BuiltInParameter.WINDOW_WIDTH) or win_type.LookupParameter("Width")
            h_param_t = win_type.get_Parameter(BuiltInParameter.WINDOW_HEIGHT) or win_type.LookupParameter("Height")
            if w_param_t and w_param_t.HasValue and w_param_t.AsDouble() > 0:
                w_val = w_param_t.AsDouble()
            if h_param_t and h_param_t.HasValue and h_param_t.AsDouble() > 0:
                h_val = h_param_t.AsDouble()

    if w_val > 0 and h_val > 0:
        area_sqft = w_val * h_val

    return area_sqft


def extract_window_u(doc):
    """
    Returns (u_value_ip, details_string, warnings_list) for the area-weighted average
    window U-value in IP units.
    
    If there are window types with custom U-values (i.e. explicitly defined by the user
    or differing from the default Revit fallback of 3.6886 W/(m2K)), we prioritize and only
    average those custom window types. Otherwise, we average all windows.
    """
    windows = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Windows)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    all_warnings = []
    type_cache = {}  # type_id -> (u_si, is_custom)

    # 1. First retrieve area and U-value details for all windows
    window_data = []  # list of (u_si, is_custom, area_sqft)
    has_any_custom = False

    for win in windows:
        type_id = win.GetTypeId()

        # Window area: try multiple parameter sources
        area_sqft = _get_window_area(win, doc)
        if area_sqft <= 0:
            continue

        # Cache per type
        if type_id not in type_cache:
            win_type = doc.GetElement(type_id)
            u_si, is_custom = (None, False)
            if win_type:
                u_si, is_custom = _get_window_u_from_type(win_type, doc)
            if u_si is None:
                all_warnings.append(
                    u"  ⚠ Window type '{}' has no thermal data"
                    .format(
                        win_type.get_Parameter(
                            BuiltInParameter.ALL_MODEL_TYPE_NAME
                        ).AsString() if win_type else "Unknown"
                    )
                )
            type_cache[type_id] = (u_si, is_custom)

        u_si, is_custom = type_cache[type_id]
        if u_si is not None:
            if is_custom:
                has_any_custom = True
            window_data.append((u_si, is_custom, area_sqft))

    # 2. Compute area-weighted average
    sum_ua = 0.0
    sum_area = 0.0

    for u_si, is_custom, area_sqft in window_data:
        # If there are custom windows in the model, ignore default/unconfigured windows
        if has_any_custom and not is_custom:
            continue
        sum_ua += u_si * area_sqft
        sum_area += area_sqft

    if sum_area > 0:
        avg_u_si = sum_ua / sum_area
        avg_u_ip = avg_u_si * cfg.SI_TO_IP_U_VALUE
        detail = (
            u"Window U-value: {:.4f} Btu/(h·ft²·°F)  "
            u"[{:.4f} W/(m²·K)]  —  from {:.0f} ft² of window area{}"
            .format(avg_u_ip, avg_u_si, sum_area, u" (using custom types only)" if has_any_custom else "")
        )
        return avg_u_ip, detail, all_warnings
    return None, "No windows with thermal data found.", all_warnings


# ------------------------------------------------------------------ #
#  Extract Gross Floor Area (GFA)
# ------------------------------------------------------------------ #
def extract_gfa(doc):
    """
    Sum floor areas in the model. Returns GFA in sq ft.
    """
    floors = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Floors)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    total = 0.0
    for fl in floors:
        p = fl.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
        if p and p.HasValue:
            total += p.AsDouble()   # sq ft

    return total if total > 0 else None


# ------------------------------------------------------------------ #
#  Extract Material Quantities for Embodied Carbon
# ------------------------------------------------------------------ #

# Strict keyword lists per category
_KEYWORDS_BRICK = ["brick", "burnt clay", "clay brick"]
_KEYWORDS_AAC = ["aac", "autoclaved"]
_KEYWORDS_CONCRETE = ["concrete block", "cement block", "concrete masonry", "cmu"]

_INSULATION_THICKNESS_MAP = {
    "25": "insulation_25",
    "50": "insulation_50",
    "75": "insulation_75",
    "100": "insulation_100",
}

_GLAZING_KEYWORDS = {
    "single": "glazing_single",
    "double": "glazing_double",
    "triple": "glazing_triple",
}


def _classify_material(name_lower):
    """Return (db_key, uses_volume) based on a lowercase material name."""

    # 1. BRICK (Highest priority check to avoid brick block masonry falling to concrete block)
    if any(kw in name_lower for kw in _KEYWORDS_BRICK):
        return "wall_brick", True
        
    # 2. AAC
    if any(kw in name_lower for kw in _KEYWORDS_AAC):
        return "wall_aac", True
        
    # 3. CONCRETE BLOCK
    if any(kw in name_lower for kw in _KEYWORDS_CONCRETE):
        return "wall_concrete", True

    # 4. Insulation (includes EIFS/EFIS)
    if "eps" in name_lower or "insulation" in name_lower or "polystyrene" in name_lower or "eifs" in name_lower or "efis" in name_lower:
        return "insulation", False

    # 5. Glazing
    if "glazing" in name_lower or "glass" in name_lower or "window" in name_lower:
        for kw, key in _GLAZING_KEYWORDS.items():
            if kw in name_lower:
                return key, False
        return "glazing_single", False  # default

    return None, False


def _get_insulation_key_from_thickness(width_ft):
    """Map a physical layer width (in feet) to the closest standard insulation database key."""
    width_mm = width_ft * 304.8
    targets = [25, 50, 75, 100]
    closest = min(targets, key=lambda x: abs(x - width_mm))
    return "insulation_{}".format(closest)


def extract_quantities(doc):
    """
    Scan all materials in the model and classify quantities into
    the EcoBIM database categories.

    Returns dict like {"wall_brick": 12.4, "insulation_50": 350.0, ...}
    """
    from Autodesk.Revit.DB import FilteredElementCollector, Material

    results = {
        "wall_brick": 0.0,
        "wall_aac": 0.0,
        "wall_concrete": 0.0,
        "insulation_25": 0.0,
        "insulation_50": 0.0,
        "insulation_75": 0.0,
        "insulation_100": 0.0,
        "glazing_single": 0.0,
        "glazing_double": 0.0,
        "glazing_triple": 0.0,
    }

    # Approach: iterate wall, roof, and floor instances and classify their
    # structural material layers, then windows.

    # --- Walls ---
    walls = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Walls)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for wall in walls:
        wall_type = doc.GetElement(wall.GetTypeId())
        if wall_type is None:
            continue
        cs = wall_type.GetCompoundStructure()
        if cs is None:
            continue
        for idx in range(cs.LayerCount):
            mat_id = cs.GetMaterialId(idx)
            if mat_id == ElementId.InvalidElementId:
                continue
            mat = doc.GetElement(mat_id)
            if mat is None:
                continue

            db_key, uses_volume = _classify_material(mat.Name.lower())
            if db_key is None:
                continue

            width_ft = cs.GetLayerWidth(idx)
            if db_key == "insulation":
                db_key = _get_insulation_key_from_thickness(width_ft)

            # Try native extraction first (takes care of cuts, joins, windows/doors subtracts)
            vol_cuft = 0.0
            area_sqft = 0.0
            try:
                vol_cuft = wall.GetMaterialVolume(mat_id)
                area_sqft = wall.GetMaterialArea(mat_id)
            except Exception:
                pass

            if uses_volume:
                if vol_cuft <= 0:
                    area_param = wall.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
                    if area_param and area_param.HasValue:
                        vol_cuft = area_param.AsDouble() * width_ft
                vol_cum = vol_cuft * (cfg.FT_TO_M ** 3)  # ft³ → m³
                results[db_key] += vol_cum
            else:
                if area_sqft <= 0:
                    area_param = wall.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
                    if area_param and area_param.HasValue:
                        area_sqft = area_param.AsDouble()
                area_sqm = area_sqft * cfg.SQFT_TO_SQM
                results[db_key] += area_sqm

    # --- Roofs ---
    roofs = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for roof in roofs:
        roof_type = doc.GetElement(roof.GetTypeId())
        if roof_type is None:
            continue
        cs = roof_type.GetCompoundStructure()
        if cs is None:
            continue
        for idx in range(cs.LayerCount):
            mat_id = cs.GetMaterialId(idx)
            if mat_id == ElementId.InvalidElementId:
                continue
            mat = doc.GetElement(mat_id)
            if mat is None:
                continue

            db_key, uses_volume = _classify_material(mat.Name.lower())
            if db_key is None:
                continue

            width_ft = cs.GetLayerWidth(idx)
            if db_key == "insulation":
                db_key = _get_insulation_key_from_thickness(width_ft)

            # Try native extraction first
            vol_cuft = 0.0
            area_sqft = 0.0
            try:
                vol_cuft = roof.GetMaterialVolume(mat_id)
                area_sqft = roof.GetMaterialArea(mat_id)
            except Exception:
                pass

            if uses_volume:
                if vol_cuft <= 0:
                    area_param = roof.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
                    if area_param and area_param.HasValue:
                        vol_cuft = area_param.AsDouble() * width_ft
                vol_cum = vol_cuft * (cfg.FT_TO_M ** 3)  # ft³ → m³
                results[db_key] += vol_cum
            else:
                if area_sqft <= 0:
                    area_param = roof.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
                    if area_param and area_param.HasValue:
                        area_sqft = area_param.AsDouble()
                area_sqm = area_sqft * cfg.SQFT_TO_SQM
                results[db_key] += area_sqm

    # --- Floors ---
    floors = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Floors)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for floor in floors:
        floor_type = doc.GetElement(floor.GetTypeId())
        if floor_type is None:
            continue
        cs = floor_type.GetCompoundStructure()
        if cs is None:
            continue
        for idx in range(cs.LayerCount):
            mat_id = cs.GetMaterialId(idx)
            if mat_id == ElementId.InvalidElementId:
                continue
            mat = doc.GetElement(mat_id)
            if mat is None:
                continue

            db_key, uses_volume = _classify_material(mat.Name.lower())
            if db_key is None:
                continue

            width_ft = cs.GetLayerWidth(idx)
            if db_key == "insulation":
                db_key = _get_insulation_key_from_thickness(width_ft)

            # Try native extraction first
            vol_cuft = 0.0
            area_sqft = 0.0
            try:
                vol_cuft = floor.GetMaterialVolume(mat_id)
                area_sqft = floor.GetMaterialArea(mat_id)
            except Exception:
                pass

            if uses_volume:
                if vol_cuft <= 0:
                    area_param = floor.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
                    if area_param and area_param.HasValue:
                        vol_cuft = area_param.AsDouble() * width_ft
                vol_cum = vol_cuft * (cfg.FT_TO_M ** 3)  # ft³ → m³
                results[db_key] += vol_cum
            else:
                if area_sqft <= 0:
                    area_param = floor.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
                    if area_param and area_param.HasValue:
                        area_sqft = area_param.AsDouble()
                area_sqm = area_sqft * cfg.SQFT_TO_SQM
                results[db_key] += area_sqm

    # --- Windows (glazing area) ---
    windows = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Windows)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for win in windows:
        win_type = doc.GetElement(win.GetTypeId())
        if win_type is None:
            continue

        # Determine glazing type from type name
        type_name = ""
        tn_param = win_type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if tn_param:
            type_name = tn_param.AsString().lower()

        db_key = None
        for kw, key in _GLAZING_KEYWORDS.items():
            if kw in type_name:
                db_key = key
                break
        if db_key is None:
            # Fallback: classify based on U-value
            u_si, _ = _get_window_u_from_type(win_type, doc)
            if u_si is not None:
                # Default Revit values:
                # - Single: 3.6886 W/(m²·K)
                # - Double: 2.0028 W/(m²·K)
                # - Triple: 1.4553 W/(m²·K)
                if u_si <= 1.75:
                    db_key = "glazing_triple"
                elif u_si <= 2.85:
                    db_key = "glazing_double"
                else:
                    db_key = "glazing_single"
            else:
                db_key = "glazing_single"  # default

        area_sqft = _get_window_area(win, doc)

        if area_sqft > 0:
            results[db_key] += area_sqft * cfg.SQFT_TO_SQM

    # Round results
    for k in results:
        results[k] = round(results[k], 2)

    return results


# ------------------------------------------------------------------ #
#  Push data to Flask backend
# ------------------------------------------------------------------ #
def push_to_backend(quantities=None, u_values=None, gfa=None):
    """
    POST data to the Flask /api/revit-push endpoint.
    Returns (success_bool, message_string).
    """
    payload = {}
    if quantities is not None:
        payload["quantities"] = quantities
    if u_values is not None:
        payload["u_values"] = u_values
    if gfa is not None:
        payload["gfa"] = gfa

    if not payload:
        return False, "Nothing to push."

    try:
        resp = _post_json(cfg.ENDPOINT_REVIT_PUSH, payload)
        if resp.get("success"):
            return True, resp.get("message", "Data pushed successfully.")
        else:
            return False, resp.get("error", "Unknown server error.")
    except Exception as e:
        return False, str(e)
