import json
import unittest
from unittest.mock import patch
from test_core import FakeRPC, options
from core import ProvisionError, RouterConnectionError, build_plan, inspect, ip_value, RPC, run_plan
from pathlib import Path
import tempfile

class DigitTests(unittest.TestCase):
    def setUp(self):
        self.rpc=FakeRPC()
        self.data=inspect(self.rpc)
    def plan(self,opt):return build_plan(self.data,opt,self.rpc.host)
    def test_arabic_persian_mixed_same_plan(self):
        expected=self.plan(options())
        for number,vlan,ip in [('١٠','١٠','١٩٢.١٦٨.١١.١٠'),('۱۰','۱۰','۱۹۲.۱۶۸.۱۱.۱۰'),('1٠','١0','192.١٦٨.11.١0')]:
            opt=options();opt.update(number=number,vlan=vlan,new_ip=ip,hostname='١٠_OpenWrt_AP')
            with self.subTest(number=number):self.assertEqual(self.plan(opt),expected)
    def test_numeric_defaults_and_direction_marks(self):
        opt=options();opt.update(number='\u200f٠٠٢٢\u061c',vlan='١٠٠٣');opt.pop('new_ip');opt.pop('hostname')
        summary=self.plan(opt)['summary']
        self.assertEqual((summary['number'],summary['vlan'],summary['new_ip'],summary['hostname']),('22','1003',self.rpc.host,'AP-22'))
        self.assertEqual(ip_value('۱۹۲.۱۶۸.۱.۲۱'),'192.168.1.21')
    def test_port_digits_do_not_change_password(self):
        with patch.object(RPC,'call',return_value={'ubus_rpc_session':'fixture'}) as call:
            rpc=RPC('١٩٢.١٦٨.١.٢١','root','١٢٣4secret','٨٠')
        self.assertEqual(rpc.port,80)
        self.assertEqual(call.call_args.args[2]['password'],'١٢٣4secret')
    def test_high_modem_numbers_with_explicit_management_ip(self):
        for value in ('255', '256', '999', '1000', '4094', '٣٠٠', '۴۰۹۴'):
            opt=options();opt.update(number=value,vlan=value,new_ip='192.168.12.7')
            opt.pop('hostname')
            summary=self.plan(opt)['summary']
            n=str(int(value))
            with self.subTest(number=value):
                self.assertEqual((summary['number'],summary['vlan'],summary['hostname'],summary['new_ip']),
                                 (n,n,'AP-'+n,'192.168.12.7'))
    def test_high_number_never_guesses_management_ip(self):
        for value in ('255', '256', '300', '4094'):
            for ip in (None, '', '   '):
                opt=options();opt.update(number=value,vlan=value)
                if ip is None:
                    opt.pop('new_ip')
                    self.assertEqual(self.plan(opt)['summary']['new_ip'],self.rpc.host)
                else:
                    opt['new_ip']=ip
                    with self.subTest(number=value,ip=ip),self.assertRaisesRegex(ProvisionError,'IPv4'):
                        self.plan(opt)
    def test_modem_number_and_ip_boundaries(self):
        for value in ('0','-1','1000000000000'):
            opt=options();opt['number']=value
            with self.subTest(number=value),self.assertRaisesRegex(ProvisionError,'12'):self.plan(opt)
        for ip in ('192.168.11.300','192.168.12.0','192.168.12.255'):
            opt=options();opt.update(number='300',vlan='300',new_ip=ip)
            with self.subTest(ip=ip),self.assertRaises(ProvisionError):self.plan(opt)
        opt=options();opt.update(number='254',vlan='254');opt.pop('new_ip')
        self.assertEqual(self.plan(opt)['summary']['new_ip'],self.rpc.host)
    def test_high_number_lan_and_independent_vlan(self):
        opt=options();opt.update(number='300',mode='lan',new_ip='192.168.11.7')
        self.assertIsNone(self.plan(opt)['summary']['vlan'])
        opt.update(mode='vlan',vlan='1200')
        self.assertEqual(self.plan(opt)['summary']['vlan'],'1200')
        for number in ('4095','10000','999999999999'):
            opt.update(number=number);opt.pop('hostname',None)
            with self.subTest(number=number):
                self.assertEqual(self.plan(opt)['summary']['number'],number)
        opt['vlan']='4095'
        with self.assertRaisesRegex(ProvisionError,'4094'):self.plan(opt)
    def test_unsupported_numeric_characters_rejected(self):
        for value in ('²','10.0','١٠٫٥','１２','-١',''):
            opt=options();opt['number']=value
            with self.subTest(value=value),self.assertRaises(ProvisionError):self.plan(opt)
    def test_android_transport_has_no_desktop_fallback(self):
        calls=[]
        def transport(url,body,timeout):
            calls.append((url,json.loads(body),timeout))
            return json.dumps({'result':[0,{'ubus_rpc_session':'fixture'}]})
        with patch('core.TRANSPORT',transport),patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('desktop network used')):
            rpc=RPC('192.168.1.21','root','temporary','٨٠')
        self.assertEqual(calls[0][0],'http://192.168.1.21:80/ubus')
        with patch('core.TRANSPORT',side_effect=ProvisionError('Selected network unavailable')),self.assertRaisesRegex(ProvisionError,'Selected network'):
            rpc.call('system','board')

    def test_wifi_disconnect_after_apply_waits_for_verified_device(self):
        original=self.rpc.call
        def disconnect(obj,method,args=None,**kwargs):
            result=original(obj,method,args,**kwargs)
            if (obj,method)==('uci','apply') and self.rpc.apply_count==1:
                raise RouterConnectionError('Wi-Fi reloading')
            return result
        self.rpc.call=disconnect
        with tempfile.TemporaryDirectory() as folder:
            result=run_plan(self.rpc,self.plan(options()),folder,lambda *a:None,sleep=lambda t:None)
        self.assertTrue(result['ok'])
        self.assertTrue(any(method=='confirm' for _,method,_ in self.rpc.calls))

    def test_device_changed_during_reconnect_never_confirms(self):
        original=self.rpc.call
        def changed(obj,method,args=None,**kwargs):
            result=original(obj,method,args,**kwargs)
            if (obj,method)==('uci','apply') and self.rpc.apply_count==1:self.rpc.mac='02:00:00:00:00:99'
            return result
        self.rpc.call=changed
        ticks=iter(range(0,1000,10))
        with tempfile.TemporaryDirectory() as folder,self.assertRaises(ProvisionError):
            run_plan(self.rpc,self.plan(options()),folder,lambda *a:None,sleep=lambda t:None,now=lambda:next(ticks))
        self.assertFalse(any(method=='confirm' for _,method,_ in self.rpc.calls))
