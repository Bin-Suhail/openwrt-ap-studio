"""OpenWrt access-point provisioning. Importing this module changes nothing."""
from __future__ import annotations
import copy
import datetime as dt
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import time
import urllib.request

TRANSPORT = None  # Android supplies a Wi-Fi/Ethernet-only HTTP transport.
DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


def normalize_digits(value):
    return str(value).translate(DIGITS).translate({ord(c): None for c in '\u200e\u200f\u061c'}).strip()


class ProvisionError(Exception):
    pass


class RouterConnectionError(ProvisionError):
    pass


def require(condition, message):
    if not condition:
        raise ProvisionError(message)


def clean(config):
    return {name: {k: v for k, v in section.items()
                   if not k.startswith('.') or k == '.type'}
            for name, section in config.items()}


def fingerprint(configs):
    value = {name: clean(config) for name, config in configs.items()}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def ip_value(value):
    value = normalize_digits(value)
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        raise ProvisionError('عنوان IP غير صحيح.') from None
    require(not address.is_multicast and not address.is_unspecified and not address.is_loopback,
            'أدخل عنوان المودم، وليس عنوانًا محليًا للكمبيوتر أو عنوان بث.')
    return value


class RPC:
    def __init__(self, host, username, password, port=80):
        self.host = ip_value(host)
        self.username = str(username).strip()
        require(bool(self.username), 'اسم المستخدم مطلوب.')
        port = normalize_digits(port)
        require(bool(re.fullmatch(r'[0-9]+', port)), 'منفذ الويب غير صحيح.')
        self.port = int(port)
        require(1 <= self.port <= 65535, 'منفذ الويب غير صحيح.')
        self.base = f'http://{self.host}:{self.port}/ubus'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.sid = '0' * 32
        self.sid = self.call('session', 'login', {
            'username': self.username, 'password': password, 'timeout': 1800
        }).get('ubus_rpc_session', '')
        require(bool(self.sid), 'لم ينجح تسجيل الدخول.')

    def call(self, obj, method, args=None, timeout=8):
        body = {'jsonrpc': '2.0', 'id': 1, 'method': 'call',
                'params': [self.sid, obj, method, args or {}]}
        request = urllib.request.Request(self.base, json.dumps(body).encode(),
                                         {'Content-Type': 'application/json'})
        try:
            if TRANSPORT is not None:
                answer = json.loads(TRANSPORT(self.base, json.dumps(body), timeout))
            else:
                with self.opener.open(request, timeout=timeout) as response:
                    answer = json.load(response)
        except ProvisionError:
            raise
        except Exception:
            raise RouterConnectionError('تعذر الاتصال بالمودم. تحقق من الكابل والعنوان ومنفذ الويب.') from None
        result = answer.get('result', [])
        require(bool(result) and result[0] == 0,
                f'لم يسمح المودم بالعملية {obj}.{method}. تحقق من بيانات الدخول وصلاحيات LuCI/RPC.')
        return result[1] if len(result) > 1 else {}

    def config(self, name):
        return self.call('uci', 'get', {'config': name}).get('values', {})

    def close(self):
        try:
            self.call('session', 'destroy', {}, timeout=2)
        except ProvisionError:
            pass


def inspect(rpc):
    board = rpc.call('system', 'board')
    require(bool(board.get('board_name')), 'هوية اللوحة غير متاحة؛ لا يمكن برمجة الجهاز بأمان.')
    configs = {name: rpc.config(name) for name in ('network', 'wireless', 'system', 'dhcp')}
    require(bool(configs['system']), 'لا تتوفر إعدادات نظام OpenWrt.')
    network = configs['network']
    lan = network.get('lan', {})
    require(lan.get('.type') == 'interface' and lan.get('proto') == 'static',
            'هذه النسخة تدعم واجهة إدارة lan ثابتة العنوان. التخطيط الموجود مختلف.')
    bridge_name = lan.get('device')
    bridge_sections = [name for name, section in network.items()
                       if section.get('.type') == 'device' and section.get('type') == 'bridge'
                       and section.get('name') == bridge_name]
    require(len(bridge_sections) == 1,
            'تخطيط الشبكة غير مدعوم: مطلوب جسر LAN بصيغة device الحديثة. أجهزة swconfig القديمة تحتاج ملف دعم آخر.')
    bridge_section = bridge_sections[0]
    bridge = network[bridge_section]
    require(bridge.get('vlan_filtering', '0') == '0' and
            not any(s.get('.type') in ('bridge-vlan', 'switch', 'switch_vlan') for s in network.values()),
            'تخطيط switch/bridge VLAN الحالي يحتاج دعمًا خاصًا؛ لن تغيّر الأداة إعداداته.')
    ports = bridge.get('ports', [])
    require(isinstance(ports, list) and bool(ports), 'لم تتعرف الأداة على منافذ جسر LAN.')
    devices = rpc.call('luci-rpc', 'getNetworkDevices')
    mac = str(devices.get(bridge_name, {}).get('mac', '')).upper()
    require(bool(re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', mac)), 'تعذر قراءة MAC جسر الإدارة.')
    # Only actual separate Ethernet ports are eligible. Never guess eth0 or a switch CPU port.
    wan_candidates = [name for name, device in devices.items()
                      if re.fullmatch(r'wan\d*', name) and not device.get('wireless')
                      and not device.get('bridge') and name not in ports]
    candidates = []
    for name, section in configs['wireless'].items():
        if section.get('.type') == 'wifi-iface' and section.get('mode') == 'ap':
            radio = configs['wireless'].get(section.get('device'), {})
            band = radio.get('band', '')
            if not band:
                band = '2g' if radio.get('hwmode') in ('11g', '11b') else '5g' if radio.get('hwmode') == '11a' else ''
            candidates.append({'section': name, 'band': band, 'ssid': section.get('ssid', ''),
                               'radio': section.get('device', ''), 'disabled': section.get('disabled', '0') == '1'})
    require(bool(candidates), 'لا توجد واجهات واي فاي في وضع Access Point.')
    try:
        watchcat = rpc.config('watchcat')
        configs['watchcat'] = watchcat
    except ProvisionError:
        watchcat = None
    try:
        watchcat_init = rpc.call('luci', 'getInitList', {'name': 'watchcat'}).get('watchcat')
    except ProvisionError:
        watchcat_init = None
    require(not rpc.call('uci', 'changes').get('changes'),
            'توجد تعديلات غير مطبقة في المودم. احفظها أو ألغها من LuCI أولًا.')
    return {'board': board, 'configs': configs, 'devices': devices,
            'bridge_name': bridge_name, 'bridge_section': bridge_section,
            'mac': mac, 'ports': ports, 'wan_candidates': wan_candidates,
            'wifi': candidates, 'watchcat_init': watchcat_init,
            'watchcat_readable': watchcat is not None, 'fingerprint': fingerprint(configs)}


def management_prefix(lan):
    addresses = lan.get('ipaddr', [])
    addresses = addresses if isinstance(addresses, list) else [addresses]
    for address in addresses:
        if '/' in str(address):
            return ipaddress.ip_interface(address).network.prefixlen
    return ipaddress.IPv4Network('0.0.0.0/' + str(lan.get('netmask', '255.255.255.0'))).prefixlen


def public_inspection(data):
    return {key: data[key] for key in ('bridge_name', 'mac', 'ports', 'wan_candidates', 'wifi',
                                      'watchcat_readable', 'watchcat_init') } | {
        'model': data['board'].get('model', data['board']['board_name']),
        'board': data['board']['board_name'], 'hostname': data['board'].get('hostname', ''),
        'version': data['board'].get('release', {}).get('version', ''),
        'kernel': data['board'].get('kernel', ''),
        'prefix': management_prefix(data['configs']['network']['lan']),
        'gateway': data['configs']['network']['lan'].get('gateway', ''),
        'dns': data['configs']['network']['lan'].get('dns', []),
        'wan_routed': [name for name, section in data['configs']['network'].items()
                       if section.get('.type') == 'interface' and name in ('wan', 'wan6')]}


def build_plan(data, options, current_host):
    desired = copy.deepcopy(data['configs'])
    number = normalize_digits(options.get('number', ''))
    require(bool(re.fullmatch(r'[0-9]{1,12}', number)) and int(number) >= 1,
            'رقم المودم يجب أن يكون رقمًا موجبًا بحد أقصى 12 خانة.')
    number = str(int(number))
    default_ip = current_host
    requested_ip = options.get('new_ip', default_ip)
    require(bool(normalize_digits(requested_ip)) if requested_ip is not None else False,
            'أدخل عنوان إدارة IPv4 صالحًا؛ رقم المودم مستقل عن عنوان IP.')
    new_ip = ip_value(requested_ip)
    prefix_text = normalize_digits(options.get('prefix', '24'))
    require(bool(re.fullmatch(r'[0-9]{1,2}', prefix_text)) and 1 <= int(prefix_text) <= 30,
            'بادئة شبكة الإدارة يجب أن تكون من 1 إلى 30.')
    prefix = int(prefix_text)
    network24 = ipaddress.ip_network(f'{new_ip}/{prefix}', strict=False)
    require(ipaddress.ip_address(new_ip) not in (network24.network_address, network24.broadcast_address),
            'عنوان الإدارة لا يجوز أن يكون عنوان الشبكة أو البث ضمن البادئة المختارة.')
    mode = options.get('mode', 'lan')
    require(mode in ('vlan', 'lan'), 'اختر VLAN أو LAN.')
    vlan = normalize_digits(options.get('vlan', number))
    if mode == 'vlan':
        require(bool(re.fullmatch(r'[0-9]+', vlan)) and 1 <= int(vlan) <= 4094, 'VLAN يجب أن يكون من 1 إلى 4094.')
        vlan = str(int(vlan))
    hostname = normalize_digits(options.get('hostname', f'AP-{number}'))
    require(bool(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}', hostname)), 'اسم الجهاز: أحرف إنجليزية وأرقام ونقطة وشرطة، حتى 63 حرفًا.')
    selection = options.get('wifi', {})
    require(isinstance(selection, dict) and bool(selection), 'اختر شبكة واي فاي واحدة على الأقل.')
    eligible = {row['section']: row for row in data['wifi']}
    require(set(selection).issubset(eligible), 'واجهة واي فاي مختارة غير موجودة.')
    network = desired['network']
    base_name = data['bridge_name']
    require(len(base_name.encode()) <= 10 or mode == 'lan', 'اسم جسر الإدارة طويل لإنشاء VLAN بشكل آمن.')
    notes = []
    ports = list(data['ports'])
    wan_enabled = bool(options.get('wan', False))
    if wan_enabled:
        if not data['wan_candidates']:
            notes.append('لا يوجد منفذ WAN منفصل خارج جسر LAN؛ تبقى المنافذ الحالية كما هي.')
        for wan in data['wan_candidates']:
            others = []
            for name, section in list(network.items()):
                if section.get('.type') == 'interface' and (section.get('device') == wan or section.get('ifname') == wan
                                                           or (name == 'wan6' and section.get('device') == '@wan')):
                    others.append(name)
            require(not others or bool(options.get('convert_wan', False)),
                    'WAN مستخدم كواجهة راوتر. وافق على تحويله إلى منفذ أكسس بوينت قبل المتابعة.')
            for name in others:
                require(name in ('wan', 'wan6'), 'منفذ WAN مستخدم بواجهة مخصصة؛ يلزم دعم التخطيط يدويًا.')
                network.pop(name)
                notes.append(f'حذف واجهة الراوتر {name} لضم المنفذ {wan} إلى جسر LAN.')
            master = data['devices'].get(wan, {}).get('master')
            require(not master or master == base_name, 'منفذ WAN تابع لجسر آخر؛ لن تنقله الأداة تلقائيًا.')
            ports.append(wan)
    network[data['bridge_section']].update(ports=ports, ageing_time='300')
    # These names are owned by this tool; unrelated VLANs are preserved.
    owned = ('apstudio_vlan', 'apstudio_bridge', 'ap_clients')
    for name in owned:
        if name in network:
            require(network[name].get('apstudio_managed') == '1', 'اسم قسم محجوز للأداة مستخدم في إعدادات أخرى.')
            network.pop(name)
    if mode == 'vlan':
        device_name = f'{base_name}.{vlan}'
        for name, section in network.items():
            require(not (section.get('.type') == 'device' and section.get('name') in (device_name, 'br-apclients')),
                    'رقم VLAN أو اسم جسر العملاء موجود مسبقًا خارج إدارة الأداة؛ اختر إعدادًا آخر.')
        network['apstudio_vlan'] = {'.type': 'device', 'type': '8021q', 'ifname': base_name,
                                   'vid': vlan, 'name': device_name, 'apstudio_managed': '1'}
        network['apstudio_bridge'] = {'.type': 'device', 'type': 'bridge', 'name': 'br-apclients',
                                     'ports': [device_name], 'ageing_time': '300', 'apstudio_managed': '1'}
        network['ap_clients'] = {'.type': 'interface', 'proto': 'none', 'device': 'br-apclients',
                                   'delegate': '0', 'apstudio_managed': '1'}
        client_network, client_bridge = 'ap_clients', 'br-apclients'
        notes.append(f'واي فاي العملاء على VLAN {vlan}. عنوان الإدارة غير موسوم على جسر LAN.')
    else:
        client_network, client_bridge = 'lan', base_name
        notes.append('واي فاي العملاء موصول مباشرة بجسر LAN بدون وسم VLAN.')
    # A rerun must not delete a network that an unselected SSID still uses.
    for name, section in desired['wireless'].items():
        nets = section.get('network', [])
        nets = nets if isinstance(nets, list) else str(nets).split()
        require(name in selection or 'ap_clients' not in nets,
                'اختر جميع الشبكات التي تستخدم جسر العملاء قبل إعادة برمجته.')
    wifi_summary = []
    for name, choice in selection.items():
        require(isinstance(choice, dict), 'إعدادات الواي فاي غير صحيحة.')
        ssid = str(choice.get('ssid', '')).strip()
        require(0 < len(ssid.encode('utf-8')) <= 32 and not any(ord(c) < 32 for c in ssid),
                'اسم الواي فاي مطلوب ولا يتجاوز 32 بايت.')
        section = desired['wireless'][name]
        section.update(network=client_network, ssid=ssid, isolate='1' if options.get('isolate', False) else '0', disabled='0')
        radio_name = section.get('device')
        require(radio_name in desired['wireless'], 'إعداد الراديو غير موجود.')
        # Explicitly enable selected AP radios, preserving channels and power settings.
        desired['wireless'][radio_name]['disabled'] = '0'
        security = choice.get('security', 'keep')
        require(security in ('keep', 'open', 'wpa2'), 'نوع حماية الواي فاي غير مدعوم.')
        if security == 'open':
            section['encryption'] = 'none'
            section.pop('key', None)
        elif security == 'wpa2':
            key = str(choice.get('key', ''))
            require(8 <= len(key.encode('utf-8')) <= 63 and not any(ord(c) < 32 for c in key),
                    'كلمة مرور WPA2 يجب أن تكون بين 8 و63 بايت.')
            section.update(encryption='psk2', key=key)
        wifi_summary.append({'section': name, 'ssid': ssid, 'band': eligible[name]['band'],
                             'security': 'مفتوحة' if section.get('encryption') == 'none' else 'محمية'})
    require('apstudio_clients' not in desired['dhcp'] or
            desired['dhcp']['apstudio_clients'].get('apstudio_managed') == '1', 'قسم DHCP محجوز للأداة موجود مسبقًا.')
    disabled = {'ignore': '1', 'dynamicdhcp': '0', 'dhcpv4': 'disabled', 'dhcpv6': 'disabled', 'ra': 'disabled'}
    for section in desired['dhcp'].values():
        if section.get('.type') == 'dhcp':
            section.update(disabled)
    desired['dhcp']['apstudio_clients'] = {'.type': 'dhcp', 'interface': client_network,
                                         'apstudio_managed': '1', **disabled}
    systems = [s for s in desired['system'].values() if s.get('.type') == 'system']
    require(len(systems) == 1, 'تخطيط إعدادات النظام غير معروف.')
    systems[0]['hostname'] = hostname
    remove_watchcat = bool(options.get('watchcat', False))
    if remove_watchcat:
        require(data['watchcat_readable'] and data['watchcat_init'] is not None,
                'لا يمكن التحقق من خدمة Watchcat. ألغِ خيار حذف المراقب أو استخدم واجهته يدويًا.')
        require(all(s.get('.type') == 'watchcat' for s in desired.get('watchcat', {}).values()),
                'إعدادات Watchcat غير متوقعة.')
        desired['watchcat'] = {}
        notes.append('حذف مهام Watchcat وإيقاف الخدمة وتعطيل بدء تشغيلها.')
    else:
        notes.append('إعدادات Watchcat تبقى كما هي.')
    network['lan'].update(ipaddr=new_ip, netmask=str(network24.netmask), delegate='0')
    if 'gateway' in options:
        gateway = normalize_digits(options.get('gateway') or '')
        if gateway:
            gateway = ip_value(gateway)
            address = ipaddress.ip_address(gateway)
            require(address in network24 and address not in (network24.network_address, network24.broadcast_address)
                    and gateway != new_ip, 'البوابة يجب أن تكون عنوانًا آخر صالحًا ضمن شبكة الإدارة.')
            network['lan']['gateway'] = gateway
        else:
            network['lan'].pop('gateway', None)
    if 'dns' in options:
        dns = str(options.get('dns') or '').split()
        require(len(dns) <= 4, 'أدخل أربعة عناوين DNS كحد أقصى.')
        if dns:
            network['lan']['dns'] = [ip_value(address) for address in dns]
        else:
            network['lan'].pop('dns', None)
    notes.append('تعطيل DHCP المحلي وDynamic DHCP وDHCPv6 وRA. تبقى كلمة مرور الإدارة كما هي.')
    notes.append('عزل عملاء الواي فاي مفعّل.' if options.get('isolate', False) else 'عزل عملاء الواي فاي غير مفعّل.')
    if new_ip != current_host:
        notes.append(f'عنوان الإدارة سيتغير إلى {new_ip}؛ قد تحتاج إعادة ضبط عنوان جهازك للوصول إليه.')
    summary = {'number': number, 'mode': mode, 'vlan': vlan if mode == 'vlan' else None,
               'new_ip': new_ip, 'prefix': prefix, 'hostname': hostname, 'ports': ports, 'wifi': wifi_summary,
               'gateway': network['lan'].get('gateway', ''), 'dns': network['lan'].get('dns', []),
               'notes': notes, 'mac': data['mac'], 'model': data['board'].get('model', ''),
               'watchcat': remove_watchcat, 'client_bridge': client_bridge}
    return {'desired': desired, 'summary': summary, 'selected_wifi': list(selection),
            'before': data, 'current_host': current_host, 'remove_watchcat': remove_watchcat}


def stage(rpc, before, after):
    for config, new_sections in after.items():
        old_sections = before.get(config, {})
        for name in old_sections.keys() - new_sections.keys():
            rpc.call('uci', 'delete', {'config': config, 'section': name})
        for name, section in new_sections.items():
            values = {k: v for k, v in section.items() if not k.startswith('.')}
            if name not in old_sections:
                added = rpc.call('uci', 'add', {'config': config, 'name': name,
                                               'type': section['.type'], 'values': values})
                require(added.get('section') == name, 'تعذر إنشاء قسم الإعدادات.')
                continue
            old = old_sections[name]
            require(old['.type'] == section['.type'], 'نوع قسم إعدادات موجود مختلف.')
            for key in set(old) - set(section):
                if not key.startswith('.'):
                    rpc.call('uci', 'delete', {'config': config, 'section': name, 'option': key})
            delta = {k: v for k, v in values.items() if old.get(k) != v}
            if delta:
                rpc.call('uci', 'set', {'config': config, 'section': name, 'values': delta})


def run_plan(rpc, plan, backup_root, progress, sleep=time.sleep, now=time.monotonic, reconnect_timeout=55):
    """Only called after the user explicitly presses Apply in the local UI."""
    data, desired, summary = plan['before'], plan['desired'], plan['summary']
    started = applied = confirmed = final_started = False
    watchcat_disabled = False
    backup_path = None
    try:
        progress('التحقق من هوية المودم وحفظ النسخة الأصلية', 8)
        live = inspect(rpc)
        require(live['mac'] == data['mac'] and live['board']['board_name'] == data['board']['board_name'],
                'تغير الجهاز المتصل. اقرأ المودم من جديد قبل البرمجة.')
        require(live['fingerprint'] == data['fingerprint'], 'تغيرت الإعدادات منذ المعاينة. اقرأ المودم من جديد.')
        stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        folder = Path(backup_root) / f"AP{summary['number']}-{stamp}"
        folder.mkdir(parents=True, exist_ok=False)
        backup_path = folder / 'before.json'
        backup_path.write_text(json.dumps({'board': data['board'], 'mac': data['mac'],
                                          'host': rpc.host, 'configs': data['configs'],
                                          'watchcat_init': data['watchcat_init']},
                                         ensure_ascii=False, indent=2), encoding='utf-8')
        (folder / 'plan.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        if plan['remove_watchcat']:
            progress('إيقاف Watchcat وتعطيل تشغيله التلقائي', 18)
            for action in ('stop', 'disable'):
                rpc.call('luci', 'setInitAction', {'name': 'watchcat', 'action': action})
            state = rpc.call('luci', 'getInitList', {'name': 'watchcat'}).get('watchcat', {})
            require(state.get('enabled') is False, 'لم يتأكد تعطيل بدء تشغيل Watchcat.')
            watchcat_disabled = True
        phase1 = copy.deepcopy(desired)
        changed_ip = summary['new_ip'] != rpc.host
        if changed_ip:
            phase1['network']['lan']['ipaddr'] = [summary['new_ip'] + '/' + str(summary['prefix']), rpc.host + '/' + str(management_prefix(data['configs']['network']['lan']))]
        progress('تجهيز الشبكة والواي فاي وإيقاف DHCP', 30)
        started = True
        stage(rpc, data['configs'], phase1)
        for config in phase1:
            require(clean(rpc.config(config)) == clean(phase1[config]), 'اختلفت الإعدادات المجهزة؛ أُلغي التطبيق.')
        progress('تطبيق الإعدادات مع مهلة رجوع تلقائي', 48)
        # Mark before the call: a connection loss may happen after the router receives it.
        applied = True
        try:
            rpc.call('uci', 'apply', {'rollback': True, 'timeout': 120})
        except RouterConnectionError:
            # A network reload can disconnect Wi-Fi after the router accepts Apply.
            # Keep polling the same authenticated device; never declare success here.
            pass
        progress('انتظار الشبكة الجديدة؛ عند انقطاع الواي فاي أعد الاتصال بالمودم خلال دقيقتين', 55)
        deadline = now() + reconnect_timeout
        last_error = ''
        while now() < deadline:
            sleep(1)
            try:
                devices = rpc.call('luci-rpc', 'getNetworkDevices')
                require(str(devices.get(data['bridge_name'], {}).get('mac', '')).upper() == data['mac'],
                        'تغير الجهاز أثناء انتظار الاتصال؛ لن تُثبت الأداة إعداداته.')
                addresses = {a.get('address') for a in devices.get(data['bridge_name'], {}).get('ipaddrs', [])}
                require(summary['new_ip'] in addresses, 'عنوان الإدارة لم يصبح نشطًا بعد.')
                require(set(summary['ports']).issubset(set(devices.get(data['bridge_name'], {}).get('ports', []))),
                        'منافذ LAN/WAN لم تصبح جاهزة بعد.')
                target_bridge = devices.get(summary['client_bridge'], {})
                require(target_bridge.get('up'), 'جسر العملاء لم يصبح جاهزًا بعد.')
                if summary['mode'] == 'vlan':
                    require(f"{data['bridge_name']}.{summary['vlan']}" in target_bridge.get('ports', []),
                            'منفذ VLAN لم يصبح جاهزًا بعد.')
                radios = rpc.call('network.wireless', 'status')
                interfaces = {row['section']: row for radio in radios.values()
                              for row in radio.get('interfaces', []) if row.get('section')}
                for selected in plan['selected_wifi']:
                    interface = interfaces.get(selected, {})
                    require(interface.get('config', {}).get('ssid') == desired['wireless'][selected]['ssid'],
                            'اسم شبكة الواي فاي لم يتحدث بعد.')
                    device = devices.get(interface.get('ifname'), {})
                    require(device.get('up') and device.get('master') == summary['client_bridge'],
                            'الواي فاي لم ينضم إلى جسر العملاء بعد.')
                for config in phase1:
                    require(clean(rpc.config(config)) == clean(phase1[config]), 'إعدادات مطبقة غير متوقعة.')
                break
            except ProvisionError as exc:
                last_error = str(exc)
        else:
            raise ProvisionError('لم يكتمل تطبيق الإعدادات؛ محاولة رجوع تلقائي. ' + last_error)
        progress('تثبيت الإعدادات التي أصبحت نشطة', 78)
        rpc.call('uci', 'confirm')
        confirmed = True
        (folder / 'applied.json').write_text(json.dumps(phase1, ensure_ascii=False, indent=2), encoding='utf-8')
        if changed_ip:
            progress('إنهاء عنوان الإدارة وإزالة عنوان البرمجة المؤقت', 90)
            rpc.call('uci', 'set', {'config': 'network', 'section': 'lan', 'values': {'ipaddr': summary['new_ip']}})
            require(clean(rpc.config('network')) == clean(desired['network']), 'اختلفت إعدادات عنوان الإدارة النهائي.')
            final_started = True
            rpc.call('uci', 'apply', {'rollback': False})
            # Config is committed synchronously; netifd reload happens afterwards.
            require(clean(rpc.config('network')) == clean(desired['network']), 'تعذر تأكيد حفظ عنوان الإدارة النهائي.')
        (folder / 'after.json').write_text(json.dumps(desired, ensure_ascii=False, indent=2), encoding='utf-8')
        progress('اكتملت البرمجة', 100)
        result = {'ok': True, 'ip': summary['new_ip'], 'number': summary['number'],
                  'mode': summary['mode'], 'vlan': summary['vlan'], 'backup': str(backup_path),
                  'message': 'اكتملت إعدادات الأكسس بوينت. تحقق من اتصال العملاء بالشبكة.'}
        (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result
    except Exception as exc:
        recovery = ''
        if started and not confirmed:
            if applied:
                try:
                    rpc.call('uci', 'rollback')
                    recovery = ' أُرسلت محاولة الرجوع؛ تأكد من الجهاز إذا انقطع الاتصال.'
                except Exception:
                    recovery = ' الرجوع التلقائي مؤقت لمدة 120 ثانية إن استلم المودم طلب التطبيق.'
            else:
                for config in desired:
                    try:
                        rpc.call('uci', 'revert', {'config': config})
                    except Exception:
                        pass
                recovery = ' أُرسلت أوامر إلغاء التعديلات المجهزة.'
        elif confirmed:
            recovery = ' الإعدادات الأساسية ثُبّتت؛ قد يبقى عنوان البرمجة المؤقت أو يكون العنوان الجديد نشطًا. اقرأ الجهاز للتأكد.'
            if not final_started:
                try:
                    rpc.call('uci', 'revert', {'config': 'network'})
                except Exception:
                    pass
        if watchcat_disabled:
            recovery += ' يبقى Watchcat معطلًا حسب اختيارك.'
        message = (str(exc) if isinstance(exc, ProvisionError) else 'حدث خطأ أثناء البرمجة.') + recovery
        if backup_path:
            message += ' النسخة الأصلية: ' + str(backup_path)
        raise ProvisionError(message) from None
