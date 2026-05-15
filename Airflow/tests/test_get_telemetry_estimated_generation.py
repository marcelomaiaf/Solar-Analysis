import ast
from pathlib import Path
import unittest


DAG_PATH = Path(__file__).resolve().parents[1] / "dags" / "get_telemetry.py"


class EstimatedGenerationDagTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DAG_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_estimated_generation_does_not_use_pvgis(self):
        self.assertNotIn("get_pvgis_hourly(", self.source)

    def test_expected_generation_uses_weather_output(self):
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "expected_generation" for target in node.targets):
                continue
            call = node.value
            self.assertIsInstance(call, ast.Call)
            self.assertIsInstance(call.func, ast.Name)
            self.assertEqual(call.func.id, "get_expected_generation")
            self.assertGreaterEqual(len(call.args), 1)
            self.assertIsInstance(call.args[0], ast.Name)
            self.assertEqual(call.args[0].id, "weather")
            return
        self.fail("expected_generation assignment not found")

    def test_weather_fetch_uses_target_date_window(self):
        self.assertIn('"start_date": target_day.isoformat()', self.source)
        self.assertIn('"end_date": target_day.isoformat()', self.source)

    def test_estimated_generation_kwh_field_is_emitted(self):
        self.assertIn('"estimated_generation_kwh"', self.source)

    def test_estimated_generation_preserves_plant_identifiers(self):
        self.assertIn('"plant_id": weather_result.get("plant_id")', self.source)
        self.assertIn('"vendor_plant_id": weather_result.get("vendor_plant_id")', self.source)

    def test_each_plant_uses_its_own_weather_target_date(self):
        self.assertIn("target_date_from_weather_result(weather_result)", self.source)

    def test_target_date_comes_from_airflow_logical_date_minus_one_day(self):
        self.assertIn("from airflow.sdk import dag, get_current_context, task", self.source)
        self.assertIn('context["logical_date"]', self.source)
        self.assertIn("- timedelta(days=1)", self.source)
        self.assertNotIn("datetime.now(tz)", self.source)

    def test_area_based_generation_uses_area_and_efficiency(self):
        self.assertIn("p.area_m2", self.source)
        self.assertIn("p.module_efficiency", self.source)
        self.assertIn('"area_m2": plant.get("area_m2")', self.source)
        self.assertIn('"module_efficiency": plant.get("module_efficiency"', self.source)
        self.assertIn('required_positive_float(weather_result.get("area_m2"), "area_m2")', self.source)
        self.assertNotIn("capacity_kwp * 1000", self.source)

    def test_telemetry_payload_can_be_converted_to_kwh(self):
        self.assertIn("def measured_generation_kwh", self.source)
        self.assertIn("sum(values) * 0.25", self.source)
        self.assertIn('"measured_generation_kwh"', self.source)

    def test_generation_report_tasks_are_wired(self):
        self.assertIn("def analyze_generation", self.source)
        self.assertIn("def generate_llm_report", self.source)
        self.assertIn("def send_generation_email", self.source)
        self.assertIn("send_email_smtp(", self.source)
        self.assertNotIn("from_email=report_sender_email", self.source)
        self.assertIn("energy_price_brl_per_kwh = 0.725", self.source)
        self.assertIn('report_sender_email = "marcelomaiaffilho@gmail.com"', self.source)
        self.assertIn('report_recipient_email = "marcelomaiaffilho@gmail.com"', self.source)


if __name__ == "__main__":
    unittest.main()
