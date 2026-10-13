import copy
import unittest
from test_core import FakeRPC, options
from core import build_plan, inspect, ProvisionError, run_plan, public_inspection
import tempfile


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.rpc = FakeRPC()
        self.data = inspect(self.rpc)

    def plan(self, **changes):
        opt = options()
        opt.update(changes)
        return build_plan(self.data, opt, self.rpc.host)

    def test_different_networks_and_prefixes(self):
        for ip, prefix, mask in [('10.50.9.7','16','255.255.0.0'),
                                 ('172.20.4.7','٢٣','255.255.254.0'),
                                 ('192.168.77.7','24','255.255.255.0'),
                                 ('10.0.0.2','30','255.255.255.252')]:
            with self.subTest(ip=ip, prefix=prefix):
                plan = self.plan(new_ip=ip, prefix=prefix)
                self.assertEqual(plan['desired']['network']['lan']['netmask'], mask)
                self.assertEqual(plan['summary']['prefix'], int(prefix))

    def test_invalid_prefix_network_and_broadcast_rejected(self):
        for changes in ({'prefix':'31'}, {'prefix':'0'}, {'prefix':'x'},
                        {'new_ip':'10.50.0.0','prefix':'16'},
                        {'new_ip':'10.50.255.255','prefix':'16'}):
            with self.subTest(changes=changes), self.assertRaises(ProvisionError):
                self.plan(**changes)

    def test_optional_gateway_and_dns(self):
        plan = self.plan(new_ip='10.50.9.7',prefix='16',gateway='١٠.٥٠.٠.١',dns='1.1.1.1 ٩.٩.٩.٩')
        lan = plan['desired']['network']['lan']
        self.assertEqual(lan['gateway'],'10.50.0.1')
        self.assertEqual(lan['dns'],['1.1.1.1','9.9.9.9'])
        for gateway in ('10.51.0.1','10.50.9.7','10.50.0.0','10.50.255.255'):
            with self.subTest(gateway=gateway), self.assertRaises(ProvisionError):
                self.plan(new_ip='10.50.9.7',prefix='16',gateway=gateway)

    def test_blank_gateway_dns_clear_existing_values(self):
        self.data['configs']['network']['lan'].update(gateway='192.168.1.1',dns=['1.1.1.1'])
        plan = self.plan(gateway='',dns='')
        self.assertNotIn('gateway',plan['desired']['network']['lan'])
        self.assertNotIn('dns',plan['desired']['network']['lan'])

    def test_generic_defaults_preserve_management_ip_and_watchcat(self):
        opt = options()
        for name in ('new_ip','mode','hostname','wan','watchcat'):
            opt.pop(name)
        plan = build_plan(self.data,opt,self.rpc.host)
        self.assertEqual(plan['summary']['new_ip'],self.rpc.host)
        self.assertEqual(plan['summary']['mode'],'lan')
        self.assertEqual(plan['summary']['hostname'],'AP-10')
        self.assertFalse(plan['remove_watchcat'])
        self.assertEqual(plan['desired']['watchcat'],self.data['configs']['watchcat'])
        self.assertNotIn('wan',plan['summary']['ports'])

    def test_client_isolation_is_optional(self):
        for value in (False,True):
            self.assertEqual(self.plan(isolate=value)['desired']['wireless']['wifi0']['isolate'],'1' if value else '0')

    def test_cross_subnet_apply_uses_correct_temporary_prefixes(self):
        self.rpc.configs['network']['lan']['netmask']='255.255.0.0'
        self.data=inspect(self.rpc)
        plan=self.plan(new_ip='172.20.4.7',prefix='23')
        with tempfile.TemporaryDirectory() as folder:
            result=run_plan(self.rpc,plan,folder,lambda *args:None,sleep=lambda t:None)
        self.assertTrue(result['ok'])
        staged=[args['values']['ipaddr'] for obj,method,args in self.rpc.calls
                if obj=='uci' and method=='set' and args.get('section')=='lan' and 'ipaddr' in args.get('values',{})]
        self.assertEqual(staged[0],['172.20.4.7/23',self.rpc.host+'/16'])
        self.assertEqual(self.rpc.configs['network']['lan']['netmask'],'255.255.254.0')

    def test_public_inspection_excludes_existing_wifi_keys(self):
        import json
        public=public_inspection(self.data)
        self.assertEqual(public['prefix'],24)
        self.assertNotIn('old-wifi-key',json.dumps(public))
