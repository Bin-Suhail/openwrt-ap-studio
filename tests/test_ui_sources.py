from html.parser import HTMLParser
from pathlib import Path
import unittest
from test_core import FakeRPC
from core import ip_value


class Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and 'id' in attrs:
            self.fields[attrs['id']] = attrs


class UISourceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.page = (self.root/'desktop/app/index.html').read_text(encoding='utf-8')
        self.inputs = Inputs()
        self.inputs.feed(self.page)

    def test_generic_defaults_have_valid_addresses_and_opt_in_changes(self):
        fields = self.inputs.fields
        self.assertEqual(ip_value(fields['host']['value']),'192.168.1.1')
        self.assertNotIn('checked',fields['wan'])
        self.assertNotIn('checked',fields['watchcat'])
        self.assertNotIn('checked',fields['isolate'])
        self.assertNotIn('checked',fields['autoIp'])
        self.assertEqual(fields['newIp'].get('value'),'')

    def test_android_and_desktop_are_identical(self):
        for name in ('core.py','server.py','index.html'):
            self.assertEqual((self.root/'desktop/app'/name).read_bytes(),
                             (self.root/'android/app/src/main/python'/name).read_bytes(),name)
