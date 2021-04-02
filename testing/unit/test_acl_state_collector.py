"""Unit tests for AclStateCollector"""

from __future__ import absolute_import

import shutil
import tempfile
import unittest

from acl_state_collector import AclStateCollector
from utils import dict_proto

from proto.acl_counts_pb2 import AclCount

from faucet import config_parser


class MockSample:
    """Mocking the metric samples"""
    def __init__(self, labels, value):
        self.labels = labels
        self.value = value


class AclStateCollectorTestBase(unittest.TestCase):
    """Base setup for AclStateCollector tests"""

    FAUCET_CONFIG = ''
    ACL_SAMPLES = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._temp_dir = None
        self._acl_collector = None

    def setUp(self):
        """Setup fixture for each test method"""
        self._temp_dir = tempfile.mkdtemp()
        _, temp_faucet_config_file = tempfile.mkstemp(dir=self._temp_dir)

        with open(temp_faucet_config_file, 'w') as file:
            file.write(self.FAUCET_CONFIG)
        _, _, dps_config, _ = config_parser.dp_parser(temp_faucet_config_file, 'fconfig')
        switches_config = {str(dp): dp for dp in dps_config}

        self._acl_collector = AclStateCollector()
        self._acl_collector.update_switch_configs(switches_config)

    def tearDown(self):
        """Cleanup after each test method finishes"""
        shutil.rmtree(self._temp_dir)
        self._acl_collector = None

    def _verify_acl_count(self, acl_count, expected_acl_count):
        self.assertEqual(acl_count, dict_proto(expected_acl_count, AclCount))


class SimpleAclStateCollectorTestCase(AclStateCollectorTestBase):
    """Basic AclStateCollector tests"""

    FAUCET_CONFIG = """
    dps:
      sec:
        dp_id: 10
        interfaces:
          1:
            acl_in: port_1_acl
            native_vlan: 1001
          2:
            acl_in: port_2_acl
            native_vlan: 1002
    acls:
      port_1_acl:
      - rule:
        description: allow dns
        cookie: 4
        actions:
          allow: True
      - rule:
        description: allow all
        cookie: 5
        actions:
          allow: True
      port_2_acl:
      - rule:
        description: allow icmp
        cookie: 6
        actions:
          allow: True
      - rule:
        description: allow ntp
        cookie: 7
        actions:
          allow: True
      - rule:
        description: allow all
        actions:
          allow: True
    """

    ACL_SAMPLES = [
        MockSample({'dp_name': 'sec', 'in_port': '1', 'cookie': 4}, 24),
        MockSample({'dp_name': 'sec', 'in_port': '1', 'cookie': 5}, 25),
        MockSample({'dp_name': 'sec', 'in_port': '2', 'cookie': 6}, 26)
    ]

    def test_get_port_acl_count(self):
        """Test getting the port ACL count"""
        acl_count = self._acl_collector.get_port_acl_count('sec', 1, self.ACL_SAMPLES)
        expected_acl_count = {
            'rules': {
                'allow dns': {'packet_count': 24},
                'allow all': {'packet_count': 25}
            }
        }
        self._verify_acl_count(acl_count, expected_acl_count)

    def test_rule_errors(self):
        """Test getting the ACL count that contains rule errors"""
        acl_count = self._acl_collector.get_port_acl_count('sec', 2, self.ACL_SAMPLES)
        expected_acl_count = {
            'rules': {
                'allow icmp': {'packet_count': 26}
            },
            'errors': [
                'No ACL metric sample available for switch, port, ACL, rule: sec, 2, port_2_acl, '
                'allow ntp (cookie=)'
            ]
        }
        self._verify_acl_count(acl_count, expected_acl_count)

    def test_nonexistent_port_config(self):
        """Testing getting ACL count for a nonexistent port"""
        acl_count = self._acl_collector.get_port_acl_count('sec', 3, self.ACL_SAMPLES)
        expected_acl_count = {
            'errors': ['Port not defined in Faucet dps config: sec, 3']
        }
        self._verify_acl_count(acl_count, expected_acl_count)


if __name__ == '__main__':
    unittest.main()
