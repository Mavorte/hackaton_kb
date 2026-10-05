"""Rychly test verejnych API:  python scripts/check_apis.py [ICO]"""
import sys
import time

from kb.clients import vies
from kb.clients.ares import Ares

ico = sys.argv[1] if len(sys.argv) > 1 else "00006947"  # Ministerstvo financi

t = time.time()
c = Ares().get(ico)
print(f"ARES {time.time() - t:.2f}s: {c.ico} {c.name} | {c.address} | zalozeno {c.founded} | DIC {c.dic}")

t = time.time()
try:
    v = vies.check_vat("CZ", ico)
    print(f"VIES {time.time() - t:.2f}s: valid={v.get('isValid')} {v.get('name')}")
except Exception as e:
    print("VIES selhalo:", e)
