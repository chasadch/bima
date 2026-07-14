import unittest
import json
import os
import sys

# Ensure current directory is in path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app import app, DEFAULT_DATABASE

class TestCarbonCostEstimator(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_predict_endpoint_default(self):
        """Test prediction API with default values"""
        payload = {
            "wall_u": 0.127,
            "roof_u": 0.1401,
            "window_u": 0.5031,
            "gfa": 13447.45
        }
        response = self.app.post(
            '/api/predict',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = json.loads(response.data.decode('utf-8'))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data['success'])
        self.assertIn('predictions', data)
        self.assertIn('eui', data['predictions'])
        self.assertIn('operational_carbon', data['predictions'])
        self.assertIn('operational_energy_kwh', data['predictions'])
        print(f"Prediction API Test Passed! Mapped Output: {data['predictions']}")

    def test_calculate_endpoint(self):
        """Test embodied carbon and cost calculator API"""
        payload = {
            "quantities": {
                "wall_brick": 10.0,      # Volume: 10 m3
                "insulation_50": 100.0,  # Area: 100 m2
                "glazing_double": 20.0   # Area: 20 m2
            }
        }
        response = self.app.post(
            '/api/calculate',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = json.loads(response.data.decode('utf-8'))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data['success'])
        
        # Verify math
        # 10 m3 * 18000 (brick cost) = 180,000
        # 100 m2 * 850 (insulation cost) = 85,000
        # 20 m2 * 13500 (double glass cost) = 270,000
        # Total cost = 535,000 PKR
        expected_cost = 10.0 * DEFAULT_DATABASE["wall_brick"]["cost"] + \
                        100.0 * DEFAULT_DATABASE["insulation_50"]["cost"] + \
                        20.0 * DEFAULT_DATABASE["glazing_double"]["cost"]
                        
        # 10 m3 * 220 (brick carbon) = 2200
        # 100 m2 * 4.4 (insulation carbon) = 440
        # 20 m2 * 22 (double glass carbon) = 440
        # Total carbon = 3080 kgCO2e
        expected_carbon = 10.0 * DEFAULT_DATABASE["wall_brick"]["carbon"] + \
                          100.0 * DEFAULT_DATABASE["insulation_50"]["carbon"] + \
                          20.0 * DEFAULT_DATABASE["glazing_double"]["carbon"]

        self.assertEqual(data['totals']['total_cost'], expected_cost)
        self.assertEqual(data['totals']['total_embodied_carbon'], expected_carbon)
        print(f"Calculator API Test Passed! Mapped Totals: Cost = {data['totals']['total_cost']} PKR, Carbon = {data['totals']['total_embodied_carbon']} kgCO2e")

    def test_revit_push_pull_endpoints(self):
        """Test pushing and pulling Revit data (quantities, U-values, GFA)"""
        # Push quantities
        payload = {
            "quantities": {
                "wall_brick": 15.0,
                "insulation_50": 120.0
            }
        }
        resp = self.app.post(
            '/api/revit-push',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data.decode('utf-8'))
        self.assertTrue(data['success'])
        
        # Pull and verify quantities are updated
        resp = self.app.get('/api/revit-pull')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data.decode('utf-8'))
        self.assertTrue(data['success'])
        self.assertEqual(data['quantities']['wall_brick'], 15.0)
        self.assertEqual(data['quantities']['insulation_50'], 120.0)
        self.assertEqual(data['u_values'], {})
        self.assertIsNone(data['gfa'])

        # Push U-values and GFA
        payload_u = {
            "u_values": {
                "wall_u": 0.12,
                "roof_u": 0.14
            },
            "gfa": 15000.0
        }
        resp = self.app.post(
            '/api/revit-push',
            data=json.dumps(payload_u),
            content_type='application/json'
        )
        self.assertEqual(resp.status_code, 200)
        
        # Pull and verify both quantities (preserved) and u_values/gfa are present
        resp = self.app.get('/api/revit-pull')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data.decode('utf-8'))
        self.assertTrue(data['success'])
        self.assertEqual(data['quantities']['wall_brick'], 15.0)
        self.assertEqual(data['u_values']['wall_u'], 0.12)
        self.assertEqual(data['u_values']['roof_u'], 0.14)
        self.assertEqual(data['gfa'], 15000.0)
        print("Revit push/pull API integration tests passed!")

if __name__ == '__main__':
    unittest.main()
