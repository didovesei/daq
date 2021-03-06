"""Gateway module for device testing"""

import datetime
import os
import shutil
from abc import ABC, abstractmethod

import logger
from env import DAQ_RUN_DIR

LOGGER = logger.get_logger('gateway')


class BaseGateway(ABC):
    """Gateway collection class for managing testing services"""

    GATEWAY_OFFSET = 0
    DUMMY_OFFSET = 1
    TEST_OFFSET_START = 2
    NUM_SET_PORTS = 6
    SET_SPACING = 10
    _PING_RETRY_COUNT = 5

    TEST_IP_FORMAT = '192.168.84.%d'

    def __init__(self, runner, name, port_set):
        self.name = name
        self.runner = runner
        assert port_set > 0, 'port_set %d, must be > 0' % port_set
        self.port_set = port_set
        self.fake_target = None
        self.host = None
        self.host_intf = None
        self.dummy = None
        self.tmpdir = None
        self.targets = {}
        self.test_ports = set()
        self.ready = set()
        self.activated = False
        self.result_linger = False
        self.dhcp_monitor = None

    def initialize(self):
        """Initialize the gateway host"""
        try:
            self._initialize()
        except Exception as e:
            LOGGER.error(
                'Gateway initialization failed, terminating: %s', str(e))
            self.terminate()
            raise

    def _initialize(self):
        host_name = 'gw%02d' % self.port_set
        host_port = self._switch_port(self.GATEWAY_OFFSET)
        LOGGER.info('Initializing gateway %s as %s/%d',
                    self.name, host_name, host_port)
        self.tmpdir = self._setup_tmpdir(host_name)
        cls = self._get_host_class()
        vol_maps = [os.path.abspath(os.path.join(DAQ_RUN_DIR, 'config')) + ':/config/inst']
        host = self.runner.add_host(
            host_name, port=host_port, cls=cls, tmpdir=self.tmpdir, vol_maps=vol_maps)
        host.activate()
        self.host = host
        LOGGER.info("Added networking host %s on port %d at %s",
                    host_name, host_port, host.IP())

        dummy_name = 'dummy%02d' % self.port_set
        dummy_port = self._switch_port(self.DUMMY_OFFSET)
        dummy = self.runner.add_host(dummy_name, port=dummy_port)
        # Dummy does not use DHCP, so need to set default route manually.
        dummy.cmd('route add -net 0.0.0.0 gw %s' % host.IP())
        self.dummy = dummy
        LOGGER.info("Added dummy target %s on port %d at %s",
                    dummy_name, dummy_port, dummy.IP())

        self.fake_target = self.TEST_IP_FORMAT % self.port_set
        self.host_intf = self.runner.get_host_interface(host)
        LOGGER.debug('Adding fake target at %s to %s',
                     self.fake_target, self.host_intf)
        host.cmd('ip addr add %s dev %s' % (self.fake_target, self.host_intf))
        ping_retry = self._PING_RETRY_COUNT
        while not self._ping_test(self.host, self.dummy):
            ping_retry -= 1
            LOGGER.info('Gateway %s warmup failed at %s with %d',
                        host_name, datetime.datetime.now(), ping_retry)
            assert ping_retry, 'warmup ping failure'

        assert self._ping_test(self.host, dummy), 'dummy ping failed'
        assert self._ping_test(dummy, host), 'host ping failed'
        assert self._ping_test(dummy, self.fake_target), 'fake ping failed'
        assert self._ping_test(
            self.host, dummy, src_addr=self.fake_target), 'reverse ping failed'

    @abstractmethod
    def _get_host_class(self):
        pass

    def activate(self):
        """Mark this gateway as activated once all hosts are present"""
        self.activated = True

    def get_base_dir(self):
        """Return the gateways base directory for instance files"""
        return os.path.abspath(self.tmpdir)

    def allocate_test_port(self):
        """Get the test port to use for this gateway setup"""
        test_port = self._switch_port(self.TEST_OFFSET_START)
        while test_port in self.test_ports:
            test_port = test_port + 1
        limit_port = self._switch_port(self.NUM_SET_PORTS)
        assert test_port < limit_port, 'no test ports available'
        self.test_ports.add(test_port)
        return test_port

    def release_test_port(self, test_port):
        """Release the given port from the gateway"""
        assert test_port in self.test_ports, 'test port not allocated'
        self.test_ports.remove(test_port)

    def _switch_port(self, offset):
        return self.port_set * self.SET_SPACING + offset

    def _is_target_expected(self, target):
        if not target:
            return False
        target_mac = target['mac']
        if target_mac in self.targets:
            return True
        LOGGER.warning('No target match found for %s in %s',
                       target_mac, self.name)
        return False

    def _dhcp_callback(self, state, target, exception=None):
        if exception:
            LOGGER.error('Gateway DHCP exception %s', exception)
        if self._is_target_expected(target) or exception:
            self.runner.ip_notify(state, target, self, exception=exception)

    def _setup_tmpdir(self, base_name):
        tmpdir = os.path.join(DAQ_RUN_DIR, base_name)
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)
        os.makedirs(tmpdir)
        return tmpdir

    def attach_target(self, device):
        """Attach the given target to this gateway; return number of attached targets."""
        assert device.mac not in self.targets, 'target %s already attached to gw' % device
        LOGGER.info('Attaching target %s to gateway group %s',
                    device, self.name)
        self.targets[device.mac] = device
        return len(self.targets)

    def detach_target(self, device):
        """Detach the given target from this gateway; return number of remaining targets."""
        assert device.mac in self.targets, 'target %s not attached to gw' % device
        LOGGER.info('Detach target %s from gateway group %s: %s',
                    device, self.name, list(self.targets.keys()))
        del self.targets[device.mac]
        return len(self.targets)

    def target_ready(self, device):
        """Mark a target ready, and return set of ready targets"""
        if device not in self.ready:
            LOGGER.info('Ready target %s from gateway group %s',
                        device, self.name)
            self.ready.add(device)
        return self.ready

    def get_targets(self):
        """Return the host targets associated with this gateway"""
        return self.targets.values()

    def get_possible_test_ports(self):
        """Return test ports associated with gateway"""
        test_port = self._switch_port(self.TEST_OFFSET_START)
        limit_port = self._switch_port(self.NUM_SET_PORTS)
        return list(range(test_port, limit_port))

    def terminate(self):
        """Terminate this instance"""
        assert not self.targets, 'gw %s has targets %s' % (
            self.name, self.targets)
        LOGGER.info('Terminating gateway %d/%s', self.port_set, self.name)
        if self.dhcp_monitor:
            self.dhcp_monitor.cleanup()
        if self.host:
            try:
                self.host.terminate()
                self.runner.remove_host(self.host)
                self.host = None
            except Exception as e:
                LOGGER.error('Gateway %s terminating host: %s', self.name, e)
                LOGGER.exception(e)
        if self.dummy:
            try:
                self.dummy.terminate()
                self.runner.remove_host(self.dummy)
                self.dummy = None
            except Exception as e:
                LOGGER.error('Gateway %s terminating dummy: %s', self.name, e)
                LOGGER.exception(e)

    def _ping_test(self, src, dst, src_addr=None):
        return self.runner.ping_test(src, dst, src_addr=src_addr)

    def __repr__(self):
        return 'Gateway group %s set %d' % (self.name, self.port_set)
