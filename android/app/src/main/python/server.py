"""Loopback-only local UI. No router access occurs until the user clicks Read."""
import argparse
import json
from pathlib import Path
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser

from core import RPC, ProvisionError, build_plan, inspect, public_inspection, run_plan

APP = Path(__file__).resolve().parent
ROOT = APP.parent
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
SESSIONS = {}
JOBS = {}
ACTIVE = None
DEMO = None
ORIGIN = ''
ANDROID = False


def expire_sessions():
    expired = [key for key, value in SESSIONS.items() if time.monotonic() - value['created'] > 900]
    for key in expired:
        value = SESSIONS.pop(key)
        if value.get('rpc'):
            value['rpc'].close()


def demo_inspection():
    raw = json.loads(Path(DEMO).read_text(encoding='utf-8'))
    class Fixture:
        host = '192.168.1.1'
        def config(self, name):
            return raw[name].get('values', {})
        def call(self, obj, method, args=None):
            if (obj, method) == ('system', 'board'):
                return raw['board']
            if (obj, method) == ('luci-rpc', 'getNetworkDevices'):
                return raw['devices']
            if (obj, method) == ('luci', 'getInitList'):
                return {'watchcat': {'enabled': True}}
            if (obj, method) == ('uci', 'changes'):
                return {'changes': {}}
            raise ProvisionError('عملية غير متاحة في العرض التجريبي.')
    return inspect(Fixture())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        # Never log request bodies, router passwords or RPC session tokens.
        pass

    def respond(self, code, value, content_type='application/json; charset=utf-8'):
        data = json.dumps(value, ensure_ascii=False).encode('utf-8') if isinstance(value, dict) else value
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'nonce-" + TOKEN + "'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def valid_host(self):
        return self.headers.get('Host') == ORIGIN.removeprefix('http://')

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403, {'error': 'طلب غير مسموح.'})
        if self.path == '/':
            page = (APP / 'index.html').read_text(encoding='utf-8')
            page = page.replace('__TOKEN__', TOKEN).replace('__DEMO__', 'true' if DEMO else 'false').replace('__ANDROID__', 'true' if ANDROID else 'false')
            return self.respond(200, page.encode('utf-8'), 'text/html; charset=utf-8')
        if self.path == '/favicon.ico':
            return self.respond(204, b'', 'image/x-icon')
        return self.respond(404, {'error': 'الصفحة غير موجودة.'})

    def do_POST(self):
        global ACTIVE
        if not self.valid_host() or self.headers.get('X-App-Token') != TOKEN or self.headers.get('Origin') != ORIGIN:
            return self.respond(403, {'error': 'طلب غير مسموح. افتح واجهة الأداة من جديد.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536:
                raise ProvisionError('حجم الطلب غير صحيح.')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ProvisionError('طلب غير صحيح.')
            if self.path == '/api/status':
                with LOCK:
                    job = JOBS.get(body.get('job'))
                    if not job:
                        raise ProvisionError('العملية غير موجودة.')
                    value = dict(job)
                return self.respond(200, value)
            with LOCK:
                if ACTIVE:
                    raise ProvisionError('توجد عملية برمجة جارية. انتظر اكتمالها.')
                expire_sessions()
                if self.path == '/api/read':
                    if len(SESSIONS) >= 12:
                        raise ProvisionError('أعد تشغيل الأداة لتنظيف الجلسات القديمة.')
                    rpc = None
                    if DEMO:
                        data = demo_inspection()
                        host = '192.168.1.1'
                    else:
                        rpc = RPC(body.get('host', ''), body.get('username', 'root'),
                                  body.get('password', ''), body.get('port', 80))
                        try:
                            data = inspect(rpc)
                        except Exception:
                            rpc.close()
                            raise
                        host = rpc.host
                    session = secrets.token_urlsafe(24)
                    SESSIONS[session] = {'rpc': rpc, 'data': data, 'host': host, 'created': time.monotonic()}
                    return self.respond(200, {'session': session, 'device': public_inspection(data),
                                              'host': host, 'demo': bool(DEMO)})
                session = SESSIONS.get(body.get('session'))
                if not session:
                    raise ProvisionError('انتهت جلسة المعاينة. اقرأ المودم من جديد.')
                if self.path == '/api/preview':
                    plan = build_plan(session['data'], body.get('options', {}), session['host'])
                    plan_id = secrets.token_urlsafe(24)
                    session.update(plan=plan, plan_id=plan_id)
                    return self.respond(200, {'plan_id': plan_id, 'summary': plan['summary'], 'demo': bool(DEMO)})
                if self.path == '/api/apply':
                    if DEMO:
                        raise ProvisionError('وضع العرض التجريبي لا يتصل بأي مودم ولا يطبّق إعدادات.')
                    if body.get('plan_id') != session.get('plan_id') or not session.get('plan'):
                        raise ProvisionError('راجع التغييرات قبل البرمجة.')
                    if body.get('ack') is not True:
                        raise ProvisionError('أكد مراجعة التغييرات أولًا.')
                    job_id = secrets.token_urlsafe(24)
                    JOBS[job_id] = {'state': 'running', 'message': 'بدء البرمجة', 'percent': 0, 'events': []}
                    ACTIVE = job_id
                    SESSIONS.pop(body['session'])
                    def work():
                        global ACTIVE
                        def progress(message, percent):
                            with LOCK:
                                JOBS[job_id].update(message=message, percent=percent)
                                JOBS[job_id]['events'].append(message)
                        try:
                            result = run_plan(session['rpc'], session['plan'], ROOT / 'backups', progress,
                                              reconnect_timeout=100 if ANDROID else 55)
                            with LOCK:
                                JOBS[job_id].update(state='done', result=result, percent=100)
                        except Exception as exc:
                            message = str(exc) if isinstance(exc, ProvisionError) else 'حدث خطأ داخلي؛ لم يتأكد اكتمال البرمجة.'
                            with LOCK:
                                JOBS[job_id].update(state='error', error=message, message='توقفت البرمجة')
                        finally:
                            session['rpc'].close()
                            with LOCK:
                                ACTIVE = None
                                # Retain the current job and the latest few results, never credentials.
                                for key in list(JOBS)[:-20]:
                                    JOBS.pop(key, None)
                    threading.Thread(target=work, daemon=True).start()
                    return self.respond(200, {'job': job_id})
                raise ProvisionError('عملية غير معروفة.')
        except (ProvisionError, ValueError, TypeError, KeyError) as exc:
            message = str(exc) if isinstance(exc, ProvisionError) else 'القيم المدخلة غير صحيحة.'
            return self.respond(400, {'error': message})
        except Exception:
            return self.respond(500, {'error': 'تعذر تنفيذ الطلب. أعد تشغيل الأداة إذا استمر الخطأ.'})


def main():
    global DEMO, ORIGIN
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--demo', nargs='?', const=str(APP.parent.parent / 'examples/demo-router.json'), help='Offline synthetic fixture; APPLY is always disabled')
    args = parser.parse_args()
    DEMO = args.demo
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    ORIGIN = f'http://127.0.0.1:{server.server_port}'
    print('OpenWrt AP Studio OpenWrt AP Tool', flush=True)
    print(ORIGIN, flush=True)
    print('Keep this window open. Ctrl+C closes the tool.', flush=True)
    if DEMO:
        print('OFFLINE DEMO: router writes are disabled.', flush=True)
    if not args.no_browser:
        webbrowser.open(ORIGIN)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
