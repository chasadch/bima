import os
import pickle
import json
import pandas as pd
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# Load ML models
models_loaded = False
eui_model = None
carbon_model = None

base_dir = os.path.dirname(os.path.abspath(__file__))
SYNC_FILE = os.path.join(base_dir, 'revit_sync.json')

def load_revit_data():
    if os.path.exists(SYNC_FILE):
        try:
            with open(SYNC_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading revit sync file: {e}")
    return {
        "quantities": {},
        "u_values": {},
        "gfa": None
    }

def save_revit_data(data):
    try:
        with open(SYNC_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Error saving revit sync file: {e}")

try:
    with open(os.path.join(base_dir, 'eui_model.pkl'), 'rb') as f:
        eui_model = pickle.load(f)
    with open(os.path.join(base_dir, 'carbon_model.pkl'), 'rb') as f:
        carbon_model = pickle.load(f)
    models_loaded = True
    print("Machine learning models loaded successfully.")
except Exception as e:
    print(f"Error loading machine learning models: {e}")


# Default database for Pakistan (material cost in PKR/unit, carbon in kgCO2e/unit)
# Units: wall block materials (per m3), EPS insulation (per m2), Glazing (per m2)
DEFAULT_DATABASE = {
    "wall_brick": {"name": "9-inch Brick Wall", "unit": "m³", "cost": 18000, "carbon": 220},
    "wall_aac": {"name": "6-inch AAC Block Wall", "unit": "m³", "cost": 15000, "carbon": 110},
    "wall_concrete": {"name": "6-inch Concrete Block Wall", "unit": "m³", "cost": 10500, "carbon": 190},
    "insulation_25": {"name": "EPS Insulation 25mm", "unit": "m²", "cost": 450, "carbon": 2.2},
    "insulation_50": {"name": "EPS Insulation 50mm", "unit": "m²", "cost": 850, "carbon": 4.4},
    "insulation_75": {"name": "EPS Insulation 75mm", "unit": "m²", "cost": 1250, "carbon": 6.6},
    "insulation_100": {"name": "EPS Insulation 100mm", "unit": "m²", "cost": 1650, "carbon": 8.8},
    "glazing_single": {"name": "Single Glazing Window", "unit": "m²", "cost": 6500, "carbon": 12.5},
    "glazing_double": {"name": "Double Glazing Window", "unit": "m²", "cost": 13500, "carbon": 22.0},
    "glazing_triple": {"name": "Triple Glazing Window", "unit": "m²", "cost": 26000, "carbon": 32.0}
}

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/predict', methods=['POST'])
def predict():
    """
    Inputs (JSON):
    - wall_u: float
    - roof_u: float
    - window_u: float
    - gfa: float (optional, default 13447.45)
    """
    if not models_loaded:
        return jsonify({"error": "ML models are not loaded on backend."}), 500
        
    try:
        data = request.get_json()
        wall_u = float(data.get('wall_u', 0.127))
        roof_u = float(data.get('roof_u', 0.1401))
        window_u = float(data.get('window_u', 0.5031))
        gfa = float(data.get('gfa', 13447.45))
        
        # Create DataFrame matching trained features exactly (including spaces)
        features = pd.DataFrame(
            [[wall_u, roof_u, window_u]], 
            columns=['Wall U value ', 'Roof U value', 'Window U value  ']
        )
        
        # Predict EUI (kBtu/ft2/year)
        predicted_eui = float(eui_model.predict(features)[0])
        
        # Predict Operational Carbon (kgCO2e/year)
        predicted_carbon = float(carbon_model.predict(features)[0])
        
        # Calculations:
        # 1. Operational Energy in kBtu/year = EUI * GFA
        operational_energy_kbtu = predicted_eui * gfa
        
        # 2. Operational Energy in kWh/year = kBtu * 0.293071
        operational_energy_kwh = operational_energy_kbtu * 0.293071
        
        # Adjust predicted carbon if GFA differs from baseline (13447.45 ft2)
        # Carbon scales proportionally with GFA
        baseline_gfa = 13447.45
        adjusted_carbon = predicted_carbon * (gfa / baseline_gfa)
        
        return jsonify({
            "success": True,
            "inputs": {
                "wall_u": wall_u,
                "roof_u": roof_u,
                "window_u": window_u,
                "gfa": gfa
            },
            "predictions": {
                "eui": round(predicted_eui, 4),
                "operational_energy_kbtu": round(operational_energy_kbtu, 2),
                "operational_energy_kwh": round(operational_energy_kwh, 2),
                "operational_carbon": round(adjusted_carbon, 2)
            }
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/calculate', methods=['POST'])
def calculate():
    """
    Calculate Embodied Carbon and Material Cost.
    Inputs (JSON):
    - quantities: dict of {material_key: quantity_value}
    - database: dict of {material_key: {cost: val, carbon: val}} (optional)
    """
    try:
        data = request.get_json()
        quantities = data.get('quantities', {})
        custom_db = data.get('database', DEFAULT_DATABASE)
        
        results = {}
        total_embodied_carbon = 0.0
        total_cost = 0.0
        
        for key, qty in quantities.items():
            qty = float(qty)
            if qty <= 0:
                continue
                
            mat_info = custom_db.get(key)
            if not mat_info:
                continue
                
            unit_cost = float(mat_info.get('cost', 0))
            unit_carbon = float(mat_info.get('carbon', 0))
            
            item_cost = qty * unit_cost
            item_carbon = qty * unit_carbon
            
            total_cost += item_cost
            total_embodied_carbon += item_carbon
            
            results[key] = {
                "name": mat_info.get('name'),
                "unit": mat_info.get('unit'),
                "quantity": qty,
                "unit_cost": unit_cost,
                "unit_carbon": unit_carbon,
                "total_cost": round(item_cost, 2),
                "total_carbon": round(item_carbon, 2)
            }
            
        return jsonify({
            "success": True,
            "materials": results,
            "totals": {
                "total_cost": round(total_cost, 2),
                "total_embodied_carbon": round(total_embodied_carbon, 2)
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/database', methods=['GET'])
def get_database():
    return jsonify(DEFAULT_DATABASE)

@app.route('/api/revit-push', methods=['POST'])
def revit_push():
    """
    Direct API endpoint for Revit / pyRevit to push quantity data, U-values, and GFA.
    Expected JSON format:
    {
      "quantities": { "wall_brick": 12.4, ... },
      "u_values": { "wall_u": 0.127, "roof_u": 0.1401, "window_u": 0.5031 },
      "gfa": 13447.45
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Missing JSON payload"}), 400
            
        current_data = load_revit_data()
        has_any = False
        if 'quantities' in data:
            current_data['quantities'] = data['quantities']
            has_any = True
        if 'u_values' in data:
            current_data['u_values'] = data['u_values']
            has_any = True
        if 'gfa' in data:
            current_data['gfa'] = data['gfa']
            has_any = True
            
        if not has_any:
            return jsonify({"success": False, "error": "Missing 'quantities', 'u_values', or 'gfa' in payload"}), 400
            
        save_revit_data(current_data)
        print(f"Received direct Revit sync: quantities={current_data.get('quantities')}, u_values={current_data.get('u_values')}, gfa={current_data.get('gfa')}")
        return jsonify({"success": True, "message": "Revit data received and cached successfully!"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/revit-pull', methods=['GET'])
def revit_pull():
    """
    Frontend endpoint to pull the latest data (quantities, U-values, GFA) pushed from Revit.
    """
    current_data = load_revit_data()
    return jsonify({
        "success": True,
        "quantities": current_data.get("quantities", {}),
        "u_values": current_data.get("u_values", {}),
        "gfa": current_data.get("gfa", None)
    })

if __name__ == '__main__':
    # Running Flask dev server
    app.run(debug=True, port=5000)
