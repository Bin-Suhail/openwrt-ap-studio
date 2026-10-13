"""Copy the shared UI/backend to Android, or verify they are identical."""
import argparse
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
for name in ('core.py', 'server.py', 'index.html'):
    source = root / 'desktop/app' / name
    target = root / 'android/app/src/main/python' / name
    if args.check:
        if source.read_bytes() != target.read_bytes():
            raise SystemExit(f'Shared source drift: {name}; run scripts/sync_android.py')
    else:
        shutil.copy2(source, target)
print('Shared sources match.' if args.check else 'Android shared sources updated.')
