import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

# Keep test backups inside the explicitly writable project, including under Windows sandboxing.
TEST_TEMP = (Path(__file__).resolve().parent / '.test-tmp').resolve()
assert TEST_TEMP.parent == Path(__file__).resolve().parent
TEST_TEMP.mkdir(exist_ok=True)
tempfile.tempdir = str(TEST_TEMP)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop/app'))
from core import ProvisionError, build_plan, clean, inspect, run_plan


def fixture():
    return {'network': {
        'loopback': {'.type': 'interface', 'proto': 'static', 'device': 'lo', 'ipaddr': '127.0.0.1'},
        'bridge': {'.type': 'device', '.anonymous': True, 'type': 'bridge', 'name': 'br-lan',
                   'ports': ['lan1', 'lan2', 'lan3'], 'ageing_time': '30'},
        'lan': {'.type': 'interface', 'proto': 'static', 'device': 'br-lan',
                'ipaddr': '192.168.1.21', 'netmask': '255.255.255.0'}},
        'wireless': {
            'radio0': {'.type': 'wifi-device', 'type': 'mac80211', 'band': '2g', 'channel': 'auto'},
            'radio1': {'.type': 'wifi-device', 'type': 'mac80211', 'band': '5g', 'channel': '36'},
            'wifi0': {'.type': 'wifi-iface', 'device': 'radio0', 'mode': 'ap', 'network': 'lan',
                      'ssid': 'Original 2G', 'encryption': 'none'},
            'wifi1': {'.type': 'wifi-iface', 'device': 'radio1', 'mode': 'ap', 'network': 'lan',
                      'ssid': 'Original 5G', 'encryption': 'psk2', 'key': 'old-wifi-key'}},
        'system': {'sys': {'.type': 'system', 'hostname': 'new-ap', 'timezone': 'UTC'}},
        'dhcp': {'lan': {'.type': 'dhcp', 'interface': 'lan', 'dhcpv4': 'server', 'ignore': '1'},
                 'odhcpd': {'.type': 'odhcpd', 'maindhcp': '0'}},
        'watchcat': {'monitor': {'.type': 'watchcat', 'mode': 'ping_reboot', 'period': '6h', 'pinghosts': '8.8.8.8'}}}


class FakeRPC:
    host = '192.168.1.21'
    def __init__(self):
        self.configs = fixture()
        self.initial = copy.deepcopy(self.configs)
        self.board = {'board_name': 'vendor,modern-board', 'model': 'Fixture AP', 'kernel': '6.6', 'release': {'version': 'Fixture'}}
        self.calls = []
        self.enabled = True
        self.runtime_ok = True
        self.apply_count = 0
        self.fail_stage = False
        self.fail_apply = False
        self.fail_final = False
        self.staged = False
        self.mac = '02:00:00:00:00:01'

    def config(self, name):
        return copy.deepcopy(self.configs.get(name, {}))

    def devices(self):
        network = self.configs['network']
        bridge = network['bridge']
        ip = network['lan']['ipaddr']
        ips = ip if isinstance(ip, list) else [ip]
        devices = {'br-lan': {'mac': self.mac, 'bridge': True, 'up': True,
                               'ports': bridge['ports'], 'ipaddrs': [{'address': s.split('/')[0]} for s in ips]},
                   'wan': {'devtype': 'dsa', 'wireless': False, 'up': False}}
        if 'wan' in bridge['ports']:
            devices['wan']['master'] = 'br-lan'
        if 'apstudio_bridge' in network:
            section = network['apstudio_bridge']
            devices['br-apclients'] = {'bridge': True, 'up': self.runtime_ok, 'ports': section['ports']}
        for name, section in self.configs['wireless'].items():
            if section['.type'] == 'wifi-iface':
                target = 'br-apclients' if section['network'] == 'ap_clients' else 'br-lan'
                devices['phy-' + name] = {'wireless': True, 'up': self.runtime_ok, 'master': target}
        return devices

    def call(self, obj, method, args=None, **kwargs):
        args = args or {}
        self.calls.append((obj, method, copy.deepcopy(args)))
        if (obj, method) == ('system', 'board'):
            return copy.deepcopy(self.board)
        if (obj, method) == ('luci-rpc', 'getNetworkDevices'):
            return self.devices()
        if (obj, method) == ('network.wireless', 'status'):
            radios = {}
            for name, section in self.configs['wireless'].items():
                if section['.type'] == 'wifi-iface':
                    radios.setdefault(section['device'], {'up': True, 'interfaces': []})['interfaces'].append(
                        {'section': name, 'ifname': 'phy-' + name, 'config': copy.deepcopy(section)})
            return radios
        if (obj, method) == ('luci', 'getInitList'):
            return {'watchcat': {'enabled': self.enabled}}
        if (obj, method) == ('luci', 'setInitAction'):
            if args['action'] == 'disable':
                self.enabled = False
            return {}
        if obj == 'uci':
            if method == 'changes':
                return {'changes': {} if not self.staged else {'network': [['set', 'lan', 'ipaddr', 'new']]}}
            if method == 'set':
                if self.fail_stage and args['config'] == 'wireless':
                    raise ProvisionError('Fixture stage failure')
                self.staged = True
                self.configs[args['config']][args['section']].update(copy.deepcopy(args['values']))
                return {}
            if method == 'add':
                self.staged = True
                self.configs[args['config']][args['name']] = {'.type': args['type'], **copy.deepcopy(args['values'])}
                return {'section': args['name']}
            if method == 'delete':
                self.staged = True
                if 'option' in args:
                    self.configs[args['config']][args['section']].pop(args['option'], None)
                else:
                    self.configs[args['config']].pop(args['section'], None)
                return {}
            if method == 'apply':
                self.apply_count += 1
                self.staged = False
                if self.fail_apply or (self.fail_final and self.apply_count == 2):
                    raise ProvisionError('Fixture connection lost')
                return {}
            if method == 'confirm':
                return {}
            if method == 'revert':
                self.configs[args['config']] = copy.deepcopy(self.initial[args['config']])
                self.staged = False
                return {}
            if method == 'rollback':
                self.configs = copy.deepcopy(self.initial)
                self.staged = False
                return {}
        raise AssertionError((obj, method, args))


def options(mode='vlan'):
    return {'number': '10', 'vlan': '10', 'mode': mode, 'new_ip': '192.168.11.10',
            'hostname': '10_OpenWrt_AP', 'wan': True, 'watchcat': True,
            'wifi': {'wifi0': {'ssid': 'NETWORK_10_4G✅️', 'security': 'keep'},
                     'wifi1': {'ssid': 'NETWORK_10_5G✅️', 'security': 'keep'}}}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.rpc = FakeRPC()
        self.data = inspect(self.rpc)

    def plan(self, opt=None):
        return build_plan(self.data, opt or options(), self.rpc.host)

    def run_fake(self, plan=None):
        with tempfile.TemporaryDirectory() as folder:
            result = run_plan(self.rpc, plan or self.plan(), folder, lambda *a: None, sleep=lambda t: None)
            backup = json.loads(Path(result['backup']).read_text(encoding='utf-8'))
            self.assertEqual(backup['configs'], self.rpc.initial)
            self.assertNotIn('password', backup)
            return result

    def test_read_and_preview_never_mutate(self):
        plan = self.plan()
        self.assertEqual(self.rpc.configs, self.rpc.initial)
        self.assertFalse(any(obj == 'uci' and method in ('set', 'add', 'delete', 'apply') for obj, method, _ in self.rpc.calls))
        self.assertIn('wan', plan['desired']['network']['bridge']['ports'])
        self.assertEqual(plan['desired']['wireless']['wifi1']['key'], 'old-wifi-key')
        self.assertEqual(plan['desired']['wireless']['radio1']['channel'], '36')

    def test_vlan_full_flow_and_backup(self):
        result = self.run_fake()
        self.assertTrue(result['ok'])
        self.assertEqual(self.rpc.apply_count, 2)
        self.assertEqual(self.rpc.configs['network']['lan']['ipaddr'], '192.168.11.10')
        self.assertEqual(self.rpc.configs['network']['apstudio_vlan']['vid'], '10')
        self.assertEqual(self.rpc.configs['watchcat'], {})
        self.assertFalse(self.rpc.enabled)
        for section in self.rpc.configs['dhcp'].values():
            if section['.type'] == 'dhcp':
                self.assertEqual(section['dynamicdhcp'], '0')
                self.assertEqual(section['dhcpv4'], 'disabled')
                self.assertEqual(section['ra'], 'disabled')

    def test_lan_full_flow(self):
        self.run_fake(self.plan(options('lan')))
        self.assertNotIn('apstudio_vlan', self.rpc.configs['network'])
        self.assertEqual(self.rpc.configs['wireless']['wifi0']['network'], 'lan')

    def test_same_management_ip_uses_one_apply(self):
        opt = options()
        opt['new_ip'] = self.rpc.host
        self.run_fake(self.plan(opt))
        self.assertEqual(self.rpc.apply_count, 1)

    def test_mac_swap_blocks_all_writes(self):
        plan = self.plan()
        self.rpc.mac = '02:00:00:00:00:02'
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ProvisionError, 'تغير الجهاز'):
            run_plan(self.rpc, plan, folder, lambda *a: None)
        self.assertEqual(self.rpc.apply_count, 0)
        self.assertEqual(self.rpc.configs, self.rpc.initial)
        self.assertTrue(self.rpc.enabled)

    def test_external_edit_blocks_all_writes(self):
        plan = self.plan()
        self.rpc.configs['wireless']['wifi0']['ssid'] = 'Changed externally'
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ProvisionError, 'تغيرت الإعدادات'):
            run_plan(self.rpc, plan, folder, lambda *a: None)
        self.assertEqual(self.rpc.apply_count, 0)

    def test_staging_failure_reverts_our_changes(self):
        self.rpc.fail_stage = True
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ProvisionError, 'إلغاء'):
            run_plan(self.rpc, self.plan(), folder, lambda *a: None)
        self.assertEqual(self.rpc.configs, self.rpc.initial)
        self.assertEqual(self.rpc.apply_count, 0)

    def test_uncertain_first_apply_attempts_rollback(self):
        self.rpc.fail_apply = True
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ProvisionError, 'الرجوع'):
            run_plan(self.rpc, self.plan(), folder, lambda *a: None)
        self.assertTrue(any(method == 'rollback' for _, method, _ in self.rpc.calls))

    def test_runtime_failure_never_confirms(self):
        self.rpc.runtime_ok = False
        ticks = iter(range(0, 1000, 10))
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ProvisionError):
            run_plan(self.rpc, self.plan(), folder, lambda *a: None, sleep=lambda t: None, now=lambda: next(ticks))
        self.assertFalse(any(method == 'confirm' for _, method, _ in self.rpc.calls))
        self.assertEqual(self.rpc.configs, self.rpc.initial)

    def test_final_disconnect_reports_uncertain_not_success(self):
        self.rpc.fail_final = True
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ProvisionError, 'الإعدادات الأساسية ثُبّتت'):
            run_plan(self.rpc, self.plan(), folder, lambda *a: None, sleep=lambda t: None)

    def test_wan_conversion_requires_explicit_option(self):
        self.rpc.configs['network']['wan'] = {'.type': 'interface', 'device': 'wan', 'proto': 'dhcp'}
        self.rpc.configs['network']['wan6'] = {'.type': 'interface', 'device': '@wan', 'proto': 'dhcpv6'}
        self.data = inspect(self.rpc)
        with self.assertRaisesRegex(ProvisionError, 'WAN مستخدم'):
            self.plan()
        opt = options()
        opt['convert_wan'] = True
        plan = self.plan(opt)
        self.assertNotIn('wan', plan['desired']['network'])
        self.assertNotIn('wan6', plan['desired']['network'])

    def test_invalid_values_and_multibyte_ssid(self):
        for key, value in [('number', '0'), ('number', '1000000000000'), ('vlan', '4095'), ('new_ip', '192.168.11.255'), ('hostname', 'bad host')]:
            opt = options()
            opt[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ProvisionError):
                self.plan(opt)
        opt = options()
        opt['wifi']['wifi0']['ssid'] = 'ش' * 17
        with self.assertRaises(ProvisionError):
            self.plan(opt)

    def test_old_switch_layout_is_rejected(self):
        self.rpc.configs['network']['switch'] = {'.type': 'switch', 'name': 'switch0'}
        with self.assertRaisesRegex(ProvisionError, 'دعمًا خاصًا'):
            inspect(self.rpc)

    def test_open_and_wpa2_selection(self):
        opt = options()
        opt['wifi']['wifi1']['security'] = 'open'
        desired = self.plan(opt)['desired']
        self.assertNotIn('key', desired['wireless']['wifi1'])
        opt['wifi']['wifi0'].update(security='wpa2', key='new-password')
        self.assertEqual(self.plan(opt)['desired']['wireless']['wifi0']['key'], 'new-password')

    def test_reprovision_to_lan_cleans_only_owned_sections(self):
        self.rpc.configs = self.plan()['desired']
        self.data = inspect(self.rpc)
        desired = self.plan(options('lan'))['desired']
        self.assertNotIn('apstudio_vlan', desired['network'])
        self.assertNotIn('apstudio_bridge', desired['network'])
        self.assertEqual(desired['wireless']['wifi0']['network'], 'lan')

    def test_missing_watchcat_requires_explicit_opt_out(self):
        self.data['watchcat_init'] = None
        with self.assertRaisesRegex(ProvisionError, 'Watchcat'):
            self.plan()
        opt = options()
        opt['watchcat'] = False
        self.assertEqual(self.plan(opt)['desired']['watchcat'], self.data['configs']['watchcat'])

    def test_unselected_managed_ssid_cannot_be_moved_indirectly(self):
        self.rpc.configs = self.plan()['desired']
        self.data = inspect(self.rpc)
        opt = options()
        opt['vlan'] = '22'
        opt['wifi'].pop('wifi1')
        with self.assertRaisesRegex(ProvisionError, 'اختر جميع الشبكات'):
            self.plan(opt)


if __name__ == '__main__':
    unittest.main(verbosity=2)
