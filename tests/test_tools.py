import sys
import types
import unittest
from unittest.mock import Mock

try:
    import boto3  # noqa: F401
except ImportError:  # Keeps the unit tests runnable without live AWS dependencies.
    boto3_stub = types.ModuleType("boto3")
    boto3_stub.client = Mock()
    boto3_stub.session = types.SimpleNamespace(Session=Mock())
    sys.modules["boto3"] = boto3_stub

    botocore_stub = types.ModuleType("botocore")
    exceptions_stub = types.ModuleType("botocore.exceptions")

    class ClientError(Exception):
        pass

    exceptions_stub.ClientError = ClientError
    sys.modules["botocore"] = botocore_stub
    sys.modules["botocore.exceptions"] = exceptions_stub

import tools


class ExecutionScopeTests(unittest.TestCase):
    def test_model_schema_cannot_supply_human_confirmation(self):
        for definition in tools.TOOL_DEFINITIONS:
            properties = definition["input_schema"].get("properties", {})
            self.assertNotIn("human_confirmed", properties)

    def test_execution_layer_rechecks_termination_confirmation(self):
        result = tools.execute_tool(
            "terminate_instance",
            {"instance_id": "i-managed"},
            human_confirmed=False,
        )
        self.assertIn("trusted human confirmation", result["error"])

    def test_execution_layer_rechecks_public_access_confirmation(self):
        result = tools.execute_tool(
            "create_s3_bucket",
            {"name": "example", "public": True},
            human_confirmed=False,
        )
        self.assertIn("trusted human confirmation", result["error"])

    def test_managed_instance_is_recognized(self):
        client = Mock()
        client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-managed",
                            "Tags": [{"Key": "CreatedBy", "Value": "platform-cli"}],
                        }
                    ]
                }
            ]
        }
        self.assertTrue(tools._is_managed_instance(client, "i-managed"))

    def test_unmanaged_instance_is_rejected(self):
        client = Mock()
        client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-unmanaged",
                            "Tags": [{"Key": "CreatedBy", "Value": "manual"}],
                        }
                    ]
                }
            ]
        }
        self.assertFalse(tools._is_managed_instance(client, "i-unmanaged"))

    def test_instance_count_excludes_terminated_instances(self):
        client = Mock()
        client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {"State": {"Name": "running"}},
                        {"State": {"Name": "stopped"}},
                        {"State": {"Name": "terminated"}},
                    ]
                }
            ]
        }
        self.assertEqual(tools._managed_instance_count(client), 2)


if __name__ == "__main__":
    unittest.main()
